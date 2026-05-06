"""Embedding service using sentence-transformers."""

from typing import List, Optional, Union
import logging
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Generates embeddings using sentence-transformers models."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize the embedding service.

        Args:
            model_name: Name of the sentence-transformers model to use
        """
        self.model_name = model_name
        logger.info(f"Loading embedding model: {model_name}")

        # Try to load the model, with helpful error messages for SSL issues.
        # trust_remote_code=True is needed for models like nomic-ai/nomic-embed-text-v1.5
        # and newer instruction-aware embedding models.
        #
        # Prefer the local cache first: SentenceTransformer otherwise issues a HEAD
        # request to huggingface.co on every startup to check for updates, which
        # adds 10+ seconds of retry noise on flaky or offline networks.
        try:
            self.model = SentenceTransformer(
                model_name, trust_remote_code=True, local_files_only=True
            )
            logger.info(f"Loaded '{model_name}' from local cache (offline).")
        except Exception:
            logger.info(f"Model '{model_name}' not in cache; downloading from HuggingFace…")
            try:
                self.model = SentenceTransformer(model_name, trust_remote_code=True)
            except Exception as e:
                error_msg = str(e).lower()
                if 'ssl' in error_msg or 'certificate' in error_msg:
                    logger.error(
                        f"SSL certificate error when downloading model '{model_name}'. "
                        f"This often happens with packaged executables. "
                        f"Try: 1) Run from source instead of exe, or "
                        f"2) Pre-download the model by running: "
                        f"python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('{model_name}')\""
                    )
                raise

        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        self._query_prompt_name: Optional[str] = None
        self._document_prompt_name: Optional[str] = None
        self._query_prompt: Optional[str] = None
        self._document_prompt: Optional[str] = None
        self._configure_prompting()

        logger.info(f"Model loaded. Embedding dimension: {self.embedding_dim}")

    def _configure_prompting(self):
        """Configure model-specific query/document prompting for retrieval."""
        model_name = self.model_name.lower()
        model_prompts = getattr(self.model, "prompts", {}) or {}

        if "qwen3-embedding" in model_name:
            if "query" in model_prompts:
                self._query_prompt_name = "query"
            else:
                self._query_prompt = (
                    "Instruct: Given a search query, retrieve relevant passages that answer the query.\n"
                    "Query: "
                )
        elif "bge" in model_name:
            if "query" in model_prompts:
                self._query_prompt_name = "query"
            else:
                self._query_prompt = "Represent this sentence for searching relevant passages: "
        elif "e5" in model_name:
            self._query_prompt = "query: "
            self._document_prompt = "passage: "

        if self._query_prompt_name or self._query_prompt or self._document_prompt_name or self._document_prompt:
            logger.info(
                "Configured retrieval prompts for embeddings "
                f"(query_prompt_name={self._query_prompt_name}, document_prompt_name={self._document_prompt_name})"
            )

    @staticmethod
    def _apply_prompt(texts: Union[str, List[str]], prompt: str) -> Union[str, List[str]]:
        """Fallback prompt application when encode() does not accept prompt args."""
        if isinstance(texts, str):
            return f"{prompt}{texts}"
        return [f"{prompt}{text}" for text in texts]

    def _encode(
        self,
        texts: Union[str, List[str]],
        prompt_name: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> np.ndarray:
        """Encode text with optional prompt support and compatibility fallback."""
        encode_kwargs = {
            "show_progress_bar": False,
            "convert_to_numpy": True,
        }

        if prompt_name:
            try:
                return self.model.encode(texts, prompt_name=prompt_name, **encode_kwargs)
            except TypeError:
                logger.debug("Model encode() does not support prompt_name; falling back")
            except Exception as e:
                logger.debug(f"Prompt-name encoding failed ({prompt_name}): {e}")

        if prompt:
            try:
                return self.model.encode(texts, prompt=prompt, **encode_kwargs)
            except TypeError:
                logger.debug("Model encode() does not support prompt; prefixing manually")
            except Exception as e:
                logger.debug(f"Prompt encoding failed; prefixing manually: {e}")
            texts = self._apply_prompt(texts, prompt)

        return self.model.encode(texts, **encode_kwargs)

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            NumPy array of shape (len(texts), embedding_dim)
        """
        if not texts:
            return np.array([]).reshape(0, self.embedding_dim)

        logger.debug(f"Generating embeddings for {len(texts)} texts")
        return self._encode(
            texts,
            prompt_name=self._document_prompt_name,
            prompt=self._document_prompt,
        )

    def embed_query(self, query: str) -> np.ndarray:
        """
        Generate embedding for a single query.

        Args:
            query: Query text

        Returns:
            NumPy array of shape (embedding_dim,)
        """
        logger.debug(f"Generating embedding for query: {query[:50]}...")
        return self._encode(
            query,
            prompt_name=self._query_prompt_name,
            prompt=self._query_prompt,
        )
