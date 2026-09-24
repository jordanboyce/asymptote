"""Embedding services: local sentence-transformers or remote Ollama.

Both services share the same duck-typed surface: `model_name`,
`embedding_dim`, `embed_texts(texts, progress_callback=None)`, and
`embed_query(query)`. Batching lives HERE, not in callers — the indexer
passes a progress_callback instead of re-batching on its own.
"""

import json
import logging
import os
import threading
import urllib.request
from typing import Callable, List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

# One shared batch size for document embedding. Callers must not re-batch.
EMBED_BATCH_SIZE = 32


class OllamaEmbeddingService:
    """Generates embeddings via Ollama's /api/embed endpoint.

    Works against a local/self-hosted Ollama daemon (no API key) or against
    Ollama Cloud (base_url https://ollama.com + api_key). Requires the chosen
    embedding model to be available on that endpoint (`ollama pull <model>`
    locally; hosted models are served on demand). No HuggingFace download,
    no sentence-transformers dependency for this path.
    """

    def __init__(
        self,
        model_name: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        api_key: str = "",
    ):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        logger.info(f"Initialising Ollama embedding: model={model_name} url={self.base_url}")
        # Probe once to verify connectivity and discover dimensionality.
        try:
            sample = self._call_api(["dimension probe"])
            self.embedding_dim = len(sample[0])
            logger.info(f"Ollama embedding ready: dim={self.embedding_dim}")
        except Exception as e:
            if api_key:
                hint = (
                    f"Check that your Ollama Cloud API key is valid and that "
                    f"'{model_name}' is an embedding model available at {self.base_url}."
                )
            else:
                hint = (
                    f"Make sure Ollama is running and the model is pulled "
                    f"(`ollama pull {model_name}`)."
                )
            raise RuntimeError(
                f"Cannot reach Ollama at {self.base_url} with model '{model_name}'. {hint} Error: {e}"
            ) from e

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        payload = json.dumps({"model": self.model_name, "input": texts}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers=headers,
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
            # In offline/air-gapped mode there is no network to fall back to:
            # fail immediately with instructions instead of letting the hub
            # client churn through retries against an unreachable host.
            if os.environ.get("HF_HUB_OFFLINE") == "1":
                raise RuntimeError(
                    f"Embedding model '{model_name}' is not in the local "
                    f"HuggingFace cache and this deployment runs in offline "
                    f"(air-gapped) mode, so it cannot be downloaded. Either "
                    f"pre-seed the cache from a connected machine (see "
                    f"docs/AIRGAP.md), switch EMBEDDING_PROVIDER to 'ollama', "
                    f"or use the model baked into the Docker image."
                )
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


class OpenAICompatibleEmbeddingService:
    """Generates embeddings via the OpenAI-style `POST {base_url}/embeddings`.

    This one request shape — `{"model": ..., "input": [...]}` in, a `data`
    list of `{"index", "embedding"}` out — is what OpenAI, Google Gemini's
    compatibility layer, Mistral, Voyage, Jina, OpenRouter, Together, LM
    Studio, vLLM and most other embedding APIs speak, so one class covers
    all of them.
    `label` is only used in error messages so a non-engineer reads
    "Google Gemini rejected the key", not a URL.
    """

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str = "",
        label: str = "",
        batch_size: int = 64,
    ):
        if not base_url:
            raise RuntimeError(
                f"{label or 'The custom embedding endpoint'} needs a base URL "
                f"(for example https://api.example.com/v1)."
            )
        if not model_name:
            raise RuntimeError(f"{label or 'The embedding provider'} needs a model name.")
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.label = label or self.base_url
        self.batch_size = batch_size
        logger.info(f"Initialising OpenAI-compatible embedding: model={model_name} url={self.base_url}")
        try:
            sample = self._call_api(["dimension probe"])
        except Exception as e:
            raise RuntimeError(self._explain(e)) from e
        self.embedding_dim = len(sample[0])
        logger.info(f"{self.label} embedding ready: dim={self.embedding_dim}")

    def _explain(self, e: Exception) -> str:
        """Turn transport errors into one sentence a person can act on."""
        import urllib.error

        if isinstance(e, urllib.error.HTTPError):
            body = ""
            try:
                body = e.read().decode(errors="replace")[:300]
            except Exception:
                pass
            # Google answers a bad key with 400 + "Please pass a valid API key".
            if e.code in (401, 403) or (e.code == 400 and "api key" in body.lower()):
                return f"{self.label} rejected the API key (HTTP {e.code}). Check the key and try again."
            if e.code == 404:
                return (
                    f"{self.label} has no model called '{self.model_name}' or the base URL "
                    f"{self.base_url} is wrong (HTTP 404). {body}"
                )
            if e.code == 402:
                # OpenRouter answers 402 when the account has no credit left.
                return (
                    f"{self.label} says this account has no credit for model "
                    f"'{self.model_name}' (HTTP 402). Add credit or choose a free model. {body}"
                )
            if e.code == 429:
                # The body usually names the limit that was hit (OpenRouter's
                # free models cap requests per minute and per day).
                return (
                    f"{self.label} is rate-limiting this key (HTTP 429). Wait a moment "
                    f"or check your plan's limits. {body}"
                )
            return f"{self.label} returned HTTP {e.code} for model '{self.model_name}'. {body}"
        if isinstance(e, urllib.error.URLError):
            return f"Could not reach {self.label} at {self.base_url}: {e.reason}"
        return f"{self.label} embedding call failed: {e}"

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        payload = json.dumps({"model": self.model_name, "input": texts}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=payload,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("data")
        if not items:
            raise ValueError(f"{self.label} returned no embeddings: {str(data)[:300]}")
        # Providers may return rows out of order; `index` is authoritative.
        ordered = sorted(items, key=lambda item: item.get("index", 0))
        vectors = [item["embedding"] for item in ordered]
        if len(vectors) != len(texts):
            raise ValueError(
                f"{self.label} returned {len(vectors)} embeddings for {len(texts)} inputs"
            )
        return vectors

    def embed_texts(
        self,
        texts: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> np.ndarray:
        if not texts:
            return np.array([]).reshape(0, self.embedding_dim)
        logger.debug(f"{self.label} embed_texts: {len(texts)} texts")
        results: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            try:
                results.extend(self._call_api(batch))
            except Exception as e:
                raise RuntimeError(self._explain(e)) from e
            if progress_callback:
                progress_callback(min(i + len(batch), len(texts)), len(texts))
        return np.array(results, dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        logger.debug(f"{self.label} embed_query: {query[:60]}…")
        try:
            return np.array(self._call_api([query])[0], dtype=np.float32)
        except Exception as e:
            raise RuntimeError(self._explain(e)) from e


# Re-exported for older imports; the catalog is the source of truth now.
from services.embedding_providers import (  # noqa: E402
    CLOUD_EMBEDDING_PROVIDERS,
    OLLAMA_CLOUD_BASE_URL,
    get_provider,
)


def resolve_embedding_api_key(provider_id: str, explicit: Optional[str] = None) -> str:
    """The key an embedding provider should use, following one chain:

    1. an explicit value (the Settings form's own key field, or a test probe)
    2. the embedding-specific key saved in config (EMBEDDING_API_KEY)
    3. for Ollama Cloud, the legacy OLLAMA_CLOUD_API_KEY / _TOKEN
    4. the team key saved on the matching AI Providers card, so a key added
       for chat is reused without pasting it twice
    """
    from config import settings

    if explicit:
        return explicit
    if settings.embedding_api_key:
        return settings.embedding_api_key
    if provider_id == "ollama_cloud" and settings.ollama_cloud_api_key:
        return settings.ollama_cloud_api_key
    entry = get_provider(provider_id) or {}
    card = entry.get("key_provider")
    if card:
        try:
            from services.app_database import app_db
            return app_db.get_agent_api_key(card) or ""
        except Exception:
            return ""
    return ""


def embedding_signature(overrides: Optional[dict] = None) -> str:
    """Stable string naming the (provider, endpoint, model) triple in effect.

    The indexer manager keys its service cache on this, so any settings
    change that would produce different vectors yields a different key.
    """
    from config import settings

    o = overrides or {}
    provider = o.get("embedding_provider", settings.embedding_provider)
    if provider == "local":
        return f"local::{o.get('embedding_model', settings.embedding_model)}"
    entry = get_provider(provider) or {}
    if provider == "ollama":
        base = o.get("ollama_base_url", settings.ollama_base_url)
    elif provider == "openai_compatible":
        base = o.get("embedding_base_url", settings.embedding_base_url)
    else:
        base = entry.get("base_url") or ""
    model = o.get("remote_embedding_model", settings.remote_embedding_model) or entry.get("default_model", "")
    return f"{provider}::{base}::{model}"


def service_signature(collection_model: Optional[str] = None, overrides: Optional[dict] = None) -> str:
    """The signature of the service create_embedding_service would build.

    Like embedding_signature(), but honours the per-collection local model
    override the factory applies — the signature must name the model that
    actually produced a collection's vectors.
    """
    from config import settings

    o = overrides or {}
    if o.get("embedding_provider", settings.embedding_provider) == "local":
        return f"local::{collection_model or o.get('embedding_model', settings.embedding_model)}"
    return embedding_signature(o)


def describe_embedding(service) -> dict:
    """Everything another system needs to embed queries compatibly.

    Vectors are only reusable with the exact model, and some models (bge,
    e5, qwen3) expect an instruction prefix on queries or passages; a reader
    that skips it gets quietly worse results rather than an error.
    """
    info = {
        "signature": getattr(service, "signature", None),
        "model": getattr(service, "model_name", None),
        "dimension": int(getattr(service, "embedding_dim", 0) or 0),
        # VectorStore L2-normalises before adding and searching (inner
        # product over unit vectors = cosine similarity).
        "normalization": "l2",
        "metric": "inner_product",
    }
    for attr in ("_query_prompt_name", "_query_prompt", "_document_prompt_name", "_document_prompt"):
        value = getattr(service, attr, None)
        if isinstance(value, str) and value:
            info[attr.lstrip("_")] = value
    return info


def create_embedding_service(
    collection_model: Optional[str] = None,
    overrides: Optional[dict] = None,
):
    """Build the embedding service and tag it with its signature.

    The signature travels with the vectors the service writes (see
    VectorStore.embedding_info), so an index can say what produced it.
    """
    service = _build_embedding_service(collection_model, overrides)
    try:
        service.signature = service_signature(collection_model, overrides)
    except Exception:  # a stub or slotted object; the signature is advisory
        pass
    return service


def _build_embedding_service(
    collection_model: Optional[str] = None,
    overrides: Optional[dict] = None,
):
    """Build the embedding service the current settings call for.

    This is the ONE place provider selection happens — the indexer manager and
    the reindex service both route through it, so a reindex can never silently
    re-embed with a different provider than query time uses.

    - "local": sentence-transformers; `collection_model` (a per-collection
      override) wins over the global EMBEDDING_MODEL.
    - "ollama": self-hosted daemon at OLLAMA_BASE_URL, no key.
    - "ollama_cloud": ollama.com with a bearer key.
    - any other catalog entry: the OpenAI-style embeddings endpoint the
      catalog names (or EMBEDDING_BASE_URL for "openai_compatible"), with
      the key from resolve_embedding_api_key.

    `overrides` lets the Settings "Test connection" probe build a service
    from values that have not been saved yet; keys are the config field
    names.
    """
    from config import settings

    o = overrides or {}
    provider = o.get("embedding_provider", settings.embedding_provider)
    entry = get_provider(provider)
    if entry is None:
        raise RuntimeError(
            f"Unknown embedding provider '{provider}'. Choose one of: "
            + ", ".join(p["id"] for p in _visible_providers())
        )
    if settings.offline_mode and provider in CLOUD_EMBEDDING_PROVIDERS:
        raise RuntimeError(
            f"Embedding provider '{provider}' sends text to an external service, "
            f"which this deployment forbids (OFFLINE_MODE). Use 'local', "
            f"'ollama', or a self-hosted 'openai_compatible' endpoint."
        )

    if provider == "local":
        model = collection_model or o.get("embedding_model", settings.embedding_model)
        return EmbeddingService(model_name=model)

    model = o.get("remote_embedding_model", settings.remote_embedding_model) or entry["default_model"]
    explicit_key = o.get("embedding_api_key")
    api_key = resolve_embedding_api_key(provider, explicit_key)
    if entry["needs_key"] and not api_key:
        where = (
            f"add the {entry['label']} key under Settings → AI Providers, or enter one "
            f"in the Embedding section"
            if entry.get("key_provider")
            else "enter one in the Embedding section of Settings"
        )
        raise RuntimeError(f"{entry['label']} embeddings need an API key: {where}.")

    if provider == "ollama":
        return OllamaEmbeddingService(
            model_name=model,
            base_url=o.get("ollama_base_url", settings.ollama_base_url),
        )
    if provider == "ollama_cloud":
        return OllamaEmbeddingService(
            model_name=model,
            base_url=OLLAMA_CLOUD_BASE_URL,
            api_key=api_key,
        )

    base_url = entry["base_url"] or o.get("embedding_base_url", settings.embedding_base_url)
    return OpenAICompatibleEmbeddingService(
        model_name=model,
        base_url=base_url,
        api_key=api_key,
        label=entry["label"],
    )


def _visible_providers():
    from services.embedding_providers import EMBEDDING_PROVIDERS
    return [p for p in EMBEDDING_PROVIDERS if not p.get("hidden")]
