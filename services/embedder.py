"""Embedding services: local sentence-transformers or remote Ollama.

Both services share the same duck-typed surface: `model_name`,
`embedding_dim`, `embed_texts(texts, progress_callback=None)`, and
`embed_query(query)`. Batching lives HERE, not in callers — the indexer
passes a progress_callback instead of re-batching on its own.
"""

import json
import logging
import threading
import urllib.request
from typing import Callable, List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

# One shared batch size for document embedding. Callers must not re-batch.
EMBED_BATCH_SIZE = 32


class OllamaEmbeddingService:
    """Generates embeddings via Ollama's /api/embed endpoint.

    Requires Ollama running locally (or reachable at base_url) with the
    chosen embedding model already pulled (e.g. `ollama pull nomic-embed-text`).
    No HuggingFace download, no sentence-transformers dependency for this path.
    """

    def __init__(
        self,
        model_name: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
    ):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        logger.info(f"Initialising Ollama embedding: model={model_name} url={self.base_url}")
        # Probe once to verify connectivity and discover dimensionality.
        try:
            sample = self._call_api(["dimension probe"])
            self.embedding_dim = len(sample[0])
            logger.info(f"Ollama embedding ready: dim={self.embedding_dim}")
        except Exception as e:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.base_url} with model '{model_name}'. "
                f"Make sure Ollama is running and the model is pulled "
                f"(`ollama pull {model_name}`). Error: {e}"
            ) from e

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        payload = json.dumps({"model": self.model_name, "input": texts}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        embeddings = data.get("embeddings")
        if not embeddings:
            raise ValueError(f"Ollama /api/embed returned no embeddings: {data}")
        return embeddings

    def embed_texts(
        self,
        texts: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> np.ndarray:
        if not texts:
            return np.array([]).reshape(0, self.embedding_dim)
        logger.debug(f"Ollama embed_texts: {len(texts)} texts")
        # Batch in chunks of 64 to avoid oversized requests.
        results: List[List[float]] = []
        for i in range(0, len(texts), 64):
            results.extend(self._call_api(texts[i : i + 64]))
            if progress_callback:
                progress_callback(min(i + 64, len(texts)), len(texts))
        return np.array(results, dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        logger.debug(f"Ollama embed_query: {query[:60]}…")
        return np.array(self._call_api([query])[0], dtype=np.float32)


class EmbeddingService:
    """Generates embeddings using sentence-transformers models."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize the embedding service.

        Args:
            model_name: Name of the sentence-transformers model to use
        """
        from sentence_transformers import SentenceTransformer  # lazy: only needed for local provider

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
        # encode() is reached from background indexing threads and live search
        # requests at once; PyTorch inference isn't guaranteed re-entrant on a
        # shared model, so serialize per batch (bounded wait for queries).
        self._encode_lock = threading.Lock()
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
        import torch  # dependency of sentence-transformers; lazy like the model import

        encode_kwargs = {
            "show_progress_bar": False,
            "convert_to_numpy": True,
        }

        with self._encode_lock, torch.inference_mode():
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

    def embed_texts(
        self,
        texts: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> np.ndarray:
        """
        Generate embeddings for a list of texts.

        Batches internally (EMBED_BATCH_SIZE) so a large document never holds
        the encode lock for its full duration, and progress can be reported
        without callers re-implementing batching.

        Args:
            texts: List of text strings to embed
            progress_callback: Optional (done, total) callback per batch

        Returns:
            NumPy array of shape (len(texts), embedding_dim)
        """
        if not texts:
            return np.array([]).reshape(0, self.embedding_dim)

        logger.debug(f"Generating embeddings for {len(texts)} texts")
        batches = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start:start + EMBED_BATCH_SIZE]
            batches.append(self._encode(
                batch,
                prompt_name=self._document_prompt_name,
                prompt=self._document_prompt,
            ))
            if progress_callback:
                progress_callback(min(start + len(batch), len(texts)), len(texts))
        return np.vstack(batches)

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
