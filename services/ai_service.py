"""AI enhancement service supporting Anthropic, OpenAI, INL HPC, and Ollama providers.

Provides optional AI-powered search enhancements:
- Result reranking: reorder results by actual relevance using LLM judgment
- Result synthesis: generate a coherent answer with citations from top results

Users provide their own API keys for cloud providers (Anthropic, OpenAI, INL HPC).
Ollama runs locally and requires no API key.
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)


def detect_ollama(base_url: str = "http://localhost:11434") -> Dict:
    """Detect if Ollama is running and list available models.

    Returns:
        dict with keys:
            - available (bool): Whether Ollama is accessible
            - models (list): List of available model dicts with 'name' and 'size'
            - error (str, optional): Error message if detection failed
    """
    import httpx

    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            data = response.json()

            models = []
            for model in data.get("models", []):
                models.append({
                    "name": model.get("name", ""),
                    "size": model.get("size", 0),
                    "modified_at": model.get("modified_at", ""),
                })

            return {
                "available": True,
                "models": models,
                "base_url": base_url,
            }

    except httpx.ConnectError:
        return {
            "available": False,
            "models": [],
            "error": "Ollama is not running or not accessible",
        }
    except Exception as e:
        return {
            "available": False,
            "models": [],
            "error": str(e),
        }


class AIProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        """Send a prompt and return {'text': str, 'usage': {'input_tokens': int, 'output_tokens': int, 'model': str}}."""

    @abstractmethod
    def validate(self) -> bool:
        """Check if the API key is valid."""

    def complete_with_image(self, prompt: str, image_base64: str, media_type: str, max_tokens: int, model: str) -> dict:
        """Send a prompt with an image and return the same dict as complete().

        Override in providers that support vision. Default raises NotImplementedError.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support vision/image input")


class AnthropicProvider(AIProvider):
    """Anthropic Claude provider."""

    FAST_MODEL = "claude-haiku-4-5-20251001"
    QUALITY_MODEL = "claude-sonnet-4-5-20250929"

    def __init__(self, api_key: str, model: Optional[str] = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        if model:
            self.FAST_MODEL = model
            self.QUALITY_MODEL = model

    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return {
            "text": response.content[0].text.strip(),
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "model": model,
            },
        }

    def complete_with_image(self, prompt: str, image_base64: str, media_type: str, max_tokens: int, model: str) -> dict:
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return {
            "text": response.content[0].text.strip(),
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "model": model,
            },
        }

    def validate(self) -> bool:
        import anthropic
        try:
            self.client.messages.create(
                model=self.FAST_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except anthropic.AuthenticationError:
            logger.warning("Anthropic authentication failed")
            return False
        except anthropic.RateLimitError as e:
            # Quota/billing issues mean the key is valid but account has no credits
            logger.warning(f"Anthropic key valid but quota/rate limit: {e}")
            return True  # Key is valid, just no credits or rate limited
        except Exception as e:
            logger.error(f"Anthropic validation error: {e}")
            raise


class OpenAIProvider(AIProvider):
    """OpenAI provider."""

    FAST_MODEL = "gpt-4o-mini"
    QUALITY_MODEL = "gpt-4o"

    def __init__(self, api_key: str, model: Optional[str] = None):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        if model:
            self.FAST_MODEL = model
            self.QUALITY_MODEL = model

    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        response = self.client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return {
            "text": response.choices[0].message.content.strip(),
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "model": model,
            },
        }

    def complete_with_image(self, prompt: str, image_base64: str, media_type: str, max_tokens: int, model: str) -> dict:
        from openai import OpenAI
        # Use a dedicated client with longer timeouts for vision (large image payloads)
        vision_client = OpenAI(api_key=self.client.api_key, timeout=120.0)
        response = vision_client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_base64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return {
            "text": response.choices[0].message.content.strip(),
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "model": model,
            },
        }

    def validate(self) -> bool:
        from openai import AuthenticationError, RateLimitError
        try:
            self.client.chat.completions.create(
                model=self.FAST_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except AuthenticationError:
            logger.warning("OpenAI authentication failed")
            return False
        except RateLimitError as e:
            # Quota/billing issues mean the key is valid but account has no credits
            # Check if it's a quota error vs rate limit
            error_message = str(e)
            if "quota" in error_message.lower() or "insufficient_quota" in error_message.lower():
                logger.warning(f"OpenAI key valid but quota exceeded: {e}")
                return True  # Key is valid, just no credits
            else:
                logger.warning(f"OpenAI rate limit: {e}")
                return True  # Key is valid, just rate limited
        except Exception as e:
            logger.error(f"OpenAI validation error: {e}")
            raise


class INLHPCProvider(AIProvider):
    """INL HPC AI provider (OpenAI-compatible API)."""

    FAST_MODEL = "gpt-oss-20b"
    QUALITY_MODEL = "gpt-oss-120b"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.hpc.inl.gov/llm/v1",
        model: Optional[str] = None,
    ):
        from openai import OpenAI
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = OpenAI(api_key=api_key, base_url=self.base_url)
        self._models_cache: List[Dict] = []

        # Mirror Ollama's single-model behavior when user chooses a model.
        if model:
            self.FAST_MODEL = model
            self.QUALITY_MODEL = model

    @property
    def available_models(self) -> List[Dict]:
        """Return the cached model list from the latest successful validation/list call."""
        return self._models_cache

    def list_models(self) -> List[Dict]:
        """List available models using the INL HPC /models endpoint."""
        import httpx

        response = httpx.get(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()

        payload = response.json()
        if isinstance(payload, dict):
            model_entries = payload.get("data")
            if model_entries is None:
                model_entries = payload.get("models", [])
        else:
            model_entries = payload

        models = []
        if isinstance(model_entries, list):
            for entry in model_entries:
                if isinstance(entry, str):
                    models.append({
                        "id": entry,
                        "object": "model",
                    })
                    continue
                if isinstance(entry, dict):
                    model_name = entry.get("id") or entry.get("name") or entry.get("model")
                    if model_name:
                        normalized = dict(entry)
                        normalized["id"] = model_name
                        models.append(normalized)

        self._models_cache = models
        return models

    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        response = self.client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (response.choices[0].message.content or "").strip()
        usage = response.usage
        return {
            "text": text,
            "usage": {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "model": model,
            },
        }

    def complete_with_image(self, prompt: str, image_base64: str, media_type: str, max_tokens: int, model: str) -> dict:
        """Send prompt + image using the INL HPC OpenAI-compatible vision API."""
        from openai import OpenAI
        vision_client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=120.0)
        response = vision_client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_base64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = (response.choices[0].message.content or "").strip()
        usage = response.usage
        return {
            "text": text,
            "usage": {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "model": model,
            },
        }

    def validate(self) -> bool:
        import httpx
        try:
            # Validation requirement: call /models with the user-provided API key.
            self.list_models()
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                logger.warning("INL HPC authentication failed")
                return False
            logger.error(f"INL HPC validation HTTP error: {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"INL HPC validation request error: {e}")
            raise
        except Exception as e:
            logger.error(f"INL HPC validation error: {e}")
            raise


class OllamaProvider(AIProvider):
    """Ollama provider for local LLM inference."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2"):
        """Initialize Ollama provider.

        Args:
            base_url: Ollama API base URL (default: http://localhost:11434)
            model: Model name to use (e.g., llama3.2, mistral, phi3)
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        # Ollama uses the model name for both fast and quality
        # Users can override by specifying different models
        self.FAST_MODEL = model
        self.QUALITY_MODEL = model

    def _openai_client(self):
        """Return an OpenAI client pointed at Ollama's OpenAI-compatible /v1/ endpoint."""
        from openai import OpenAI
        return OpenAI(
            base_url=f"{self.base_url}/v1",
            api_key="ollama",  # Ollama ignores the key but the client requires a non-empty value
            timeout=300.0,
        )

    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        """Generate completion using Ollama's native /api/chat endpoint."""
        import httpx
        with httpx.Client(timeout=300.0) as client:
            response = client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                },
            )
            response.raise_for_status()
            data = response.json()

        text = (data.get("message", {}).get("content") or "").strip()
        return {
            "text": text,
            "usage": {
                "input_tokens": data.get("prompt_eval_count", len(prompt) // 4),
                "output_tokens": data.get("eval_count", len(text) // 4),
                "model": model,
            },
        }

    def complete_with_image(self, prompt: str, image_base64: str, media_type: str, max_tokens: int, model: str) -> dict:
        """Send prompt + image using Ollama's native /api/chat endpoint.

        The OpenAI-compatible /v1/chat/completions endpoint defaults to
        num_ctx=2048, which is far too small for page images and causes the
        model to silently return empty output.  The native endpoint lets us
        set num_ctx explicitly, matching how complete() already works.
        """
        import httpx

        # Native Ollama API takes raw base64 (no data-URI prefix)
        raw_b64 = image_base64.split(",", 1)[-1] if "," in image_base64 else image_base64

        try:
            with httpx.Client(timeout=300.0) as client:
                response = client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": prompt,
                            "images": [raw_b64],
                        }],
                        "stream": False,
                        "options": {
                            "num_predict": max_tokens,
                            "num_ctx": 16384,   # large enough for full-page images
                        },
                    },
                )
                if response.status_code == 404:
                    raise ValueError(
                        f"Ollama model '{model}' not found. Run: ollama pull {model}"
                    )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Ollama vision request failed: {exc}") from exc

        text = (data.get("message", {}).get("content") or "").strip()
        return {
            "text": text,
            "usage": {
                "input_tokens": data.get("prompt_eval_count", 0),
                "output_tokens": data.get("eval_count", len(text) // 4),
                "model": model,
            },
        }

    def validate(self) -> bool:
        """Check if Ollama is running and model is available."""
        import httpx

        try:
            with httpx.Client(timeout=5.0) as client:
                # Check if Ollama is running
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()

                # Check if the specified model is available
                data = response.json()
                models = [m.get("name", "") for m in data.get("models", [])]

                # Ollama model names can have tags (e.g., "llama3.2:latest")
                # Check if our model exists with or without tag
                model_exists = any(
                    self.model in model_name or model_name.startswith(f"{self.model}:")
                    for model_name in models
                )

                if not model_exists:
                    logger.warning(f"Ollama model '{self.model}' not found. Available models: {models}")
                    return False

                logger.info(f"Ollama validation successful. Model '{self.model}' is available.")
                return True

        except httpx.ConnectError:
            logger.warning("Ollama is not running or not accessible at {self.base_url}")
            return False
        except Exception as e:
            logger.error(f"Ollama validation error: {e}")
            return False


def create_provider(provider_name: str, api_key: str = None, **kwargs) -> AIProvider:
    """Create an AI provider instance.

    Args:
        provider_name: Provider name (anthropic, openai, inl_hpc, ollama)
        api_key: API key for cloud providers (not needed for Ollama)
        **kwargs: Additional provider-specific arguments
            - For INL HPC: base_url (default: https://api.hpc.inl.gov/llm/v1)
              and model (optional; if set, used for both rerank and synthesis)
            - For Ollama: base_url (default: http://localhost:11434), model (default: llama3.2)
    """
    if provider_name == "anthropic":
        if not api_key:
            raise ValueError("API key required for Anthropic")
        model = kwargs.get("model")
        return AnthropicProvider(api_key, model=model)
    elif provider_name == "openai":
        if not api_key:
            raise ValueError("API key required for OpenAI")
        model = kwargs.get("model")
        return OpenAIProvider(api_key, model=model)
    elif provider_name == "inl_hpc":
        if not api_key:
            raise ValueError("API key required for INL HPC")
        base_url = kwargs.get("base_url", "https://api.hpc.inl.gov/llm/v1")
        model = kwargs.get("model")
        return INLHPCProvider(api_key=api_key, base_url=base_url, model=model)
    elif provider_name == "ollama":
        base_url = kwargs.get("base_url", "http://localhost:11434")
        model = kwargs.get("model", "llama3.2")
        return OllamaProvider(base_url=base_url, model=model)
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


class AIService:
    """Provider-agnostic AI service for search enhancements."""

    def __init__(self, provider: AIProvider):
        self.provider = provider

    @property
    def fast_model(self) -> str:
        return self.provider.FAST_MODEL

    @property
    def quality_model(self) -> str:
        return self.provider.QUALITY_MODEL

    def rerank_results(
        self, query: str, results: List[dict], top_k: int
    ) -> dict:
        """Rerank search results by true relevance to the query.

        This is where AI adds genuine value over cosine similarity:
        vectors measure semantic nearness, but an LLM can judge whether
        a chunk actually answers the question.
        """
        if not results:
            return {"reranked_indices": [], "usage": None}

        snippets = []
        for r in results:
            snippets.append(f"[{r['index']}] (file: {r['filename']}) {r['text_snippet'][:200]}")
        snippet_text = "\n\n".join(snippets)

        prompt = (
            "You are a search result reranking assistant. Given a query and numbered "
            "search results, return the indices of the most relevant results ordered "
            "from most to least relevant.\n\n"
            "Rules:\n"
            "- Return ONLY a JSON array of index numbers, e.g. [3, 1, 7, 2]\n"
            f"- Return at most {top_k} indices\n"
            "- Rank by actual relevance to the query, not just keyword overlap\n"
            "- Exclude results that are not relevant at all\n\n"
            f"Query: {query}\n\n"
            f"Results:\n{snippet_text}"
        )

        start = time.time()
        response = self.provider.complete(prompt, max_tokens=300, model=self.fast_model)
        elapsed = time.time() - start

        raw = response["text"]
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            indices = json.loads(raw)
            if not isinstance(indices, list):
                raise ValueError("Expected a JSON array")
            indices = [int(i) for i in indices]
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse rerank response: {e}. Using original order.")
            indices = [r["index"] for r in results[:top_k]]

        logger.info(f"Reranked {len(results)} results in {elapsed:.2f}s -> top {len(indices)}")

        return {
            "reranked_indices": indices,
            "usage": response["usage"],
        }

    def reformulate_query(self, conversation_history: List[dict], latest_question: str) -> str:
        """Rewrite the user's latest question into a self-contained search query.

        Follow-up questions like "what date was that?" or "tell me more about maverick"
        reference entities established earlier in the conversation. Without reformulation
        the vector search has no access to that prior context and will fail to retrieve
        the right chunks. This step expands the question using conversation history so the
        search query is fully self-contained.

        Returns the reformulated query string (falls back to latest_question on any error).
        """
        if not conversation_history:
            return latest_question

        history_lines = []
        for msg in conversation_history:
            prefix = "User" if msg.get("role") == "user" else "Assistant"
            history_lines.append(f"{prefix}: {msg.get('content', '')}")
        history_text = "\n".join(history_lines)

        prompt = (
            "You are a query reformulation assistant. Your job is to rewrite a follow-up "
            "question into a standalone, search-optimized query by resolving any pronouns "
            "or references using the conversation history.\n\n"
            "Rules:\n"
            "- Output ONLY the reformulated search query — no explanation, no quotes\n"
            "- Expand abbreviations and pronouns using context (e.g. 'that receipt' → "
            "'Maverick Adventure First Stop receipt')\n"
            "- Keep it concise (10–20 words max)\n"
            "- If the question is already self-contained, return it unchanged\n\n"
            f"Conversation history:\n{history_text}\n\n"
            f"Follow-up question: {latest_question}\n\n"
            "Reformulated search query:"
        )

        try:
            response = self.provider.complete(prompt, max_tokens=80, model=self.fast_model)
            reformulated = response["text"].strip().strip('"').strip("'")
            if reformulated:
                logger.info(f"Query reformulated: {latest_question!r} → {reformulated!r}")
                return reformulated
        except Exception as e:
            logger.warning(f"Query reformulation failed, using original: {e}")

        return latest_question

    def synthesize_results(self, query: str, results: List[dict]) -> dict:
        """Generate a coherent answer from search results with citations.

        This is the highest-value AI feature: it turns "here are 10 document
        chunks" into "here is the answer to your question, with sources."
        """
        if not results:
            return {"synthesis": "", "usage": None}

        context_parts = []
        for i, r in enumerate(results):
            context_parts.append(
                f"[Source {i + 1}: {r['filename']}, page {r['page_number']}]\n"
                f"{r['text_snippet']}"
            )
        context = "\n\n---\n\n".join(context_parts)

        prompt = (
            "You are a research assistant. Based on the search results below, "
            "provide a clear, concise answer to the user's query. Cite your sources "
            "using [Source N] notation.\n\n"
            "Rules:\n"
            "- Only use information from the provided sources\n"
            "- Cite specific sources for each claim using [Source N]\n"
            "- If the sources don't contain enough info, say so\n"
            "- Be concise but thorough\n"
            "- Use plain language\n\n"
            f"Query: {query}\n\n"
            f"Sources:\n{context}"
        )

        start = time.time()
        response = self.provider.complete(prompt, max_tokens=1024, model=self.quality_model)
        elapsed = time.time() - start

        logger.info(f"Synthesized answer in {elapsed:.2f}s ({len(response['text'])} chars)")

        return {
            "synthesis": response["text"],
            "usage": response["usage"],
        }
