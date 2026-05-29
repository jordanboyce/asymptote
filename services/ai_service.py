"""AI enhancement service supporting Anthropic, OpenAI, and Ollama providers.

Provides optional AI-powered search enhancements:
- Result reranking: reorder results by actual relevance using LLM judgment
- Result synthesis: generate a coherent answer with citations from top results

Users provide their own API keys for cloud providers (Anthropic, OpenAI).
Ollama runs locally and requires no API key.
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Dict

logger = logging.getLogger(__name__)


# Known-model capability hints. Keys are model ids as advisors will see them
# in settings. Anything not in this map surfaces as "unknown" rather than
# claiming a capability we can't back up — see ProviderCapabilities.
#
# Sources: provider docs as of 2026-04. Add entries when shipping support for
# a new model; do NOT guess. An unknown model quietly degrades to ReAct or to
# a clear "this model can't do tool calling" error in the UI.
KNOWN_MODELS: Dict[str, Dict[str, object]] = {
    # Anthropic — `thinking: True` means extended-thinking eligible (Claude 4
    # family). Used by AgenticEngine to gate the per-turn opt-in: callers may
    # ask for extended_thinking on every turn, but it only flows through to
    # the API when the configured model can actually run it.
    "claude-haiku-4-5-20251001":   {"tools": True,  "vision": True,  "context_window": 200_000, "thinking": True},
    "claude-sonnet-4-5-20250929":  {"tools": True,  "vision": True,  "context_window": 200_000, "thinking": True},
    "claude-opus-4-7":             {"tools": True,  "vision": True,  "context_window": 1_000_000, "thinking": True},
    "claude-sonnet-4-6":           {"tools": True,  "vision": True,  "context_window": 200_000, "thinking": True},
    # OpenAI
    "gpt-4o":                      {"tools": True,  "vision": True,  "context_window": 128_000},
    "gpt-4o-mini":                 {"tools": True,  "vision": True,  "context_window": 128_000},
    "gpt-4-turbo":                 {"tools": True,  "vision": True,  "context_window": 128_000},
    "gpt-3.5-turbo":               {"tools": True,  "vision": False, "context_window": 16_385},
    # Grok
    "grok-3":                      {"tools": True,  "vision": False, "context_window": 131_072},
    "grok-3-mini":                 {"tools": True,  "vision": False, "context_window": 131_072},
    # Google Gemini (via OpenAI-compatible endpoint)
    "gemini-2.0-flash":            {"tools": True,  "vision": True,  "context_window": 1_048_576},
    "gemini-2.5-pro-preview-03-25":{"tools": True,  "vision": True,  "context_window": 1_048_576},
    # GitHub Models (namespaced)
    "openai/gpt-4o":               {"tools": True,  "vision": True,  "context_window": 128_000},
    "openai/gpt-4o-mini":          {"tools": True,  "vision": True,  "context_window": 128_000},
    "meta/Llama-3.3-70B-Instruct": {"tools": True,  "vision": False, "context_window": 128_000},
    # Ollama Cloud (gpt-oss family supports tool calling)
    "gpt-oss:20b":                 {"tools": True,  "vision": False, "context_window": 128_000},
    "gpt-oss:120b":                {"tools": True,  "vision": False, "context_window": 128_000},
    # Ollama Cloud — Gemma 4 (Google open weights). 31b is free-tier-eligible
    # at the time of writing; per the model page, text+image only (audio is
    # advertised at the family level but not on the 31b variant).
    "gemma4:31b":                  {"tools": True,  "vision": True,  "context_window": 256_000},
    # Ollama local (default — the LocalOllamaProvider uses ReAct, not native tools)
    "llama3.2":                    {"tools": False, "vision": False, "context_window": 128_000},
    "llama3.1":                    {"tools": False, "vision": False, "context_window": 128_000},
}


@dataclass
class ProviderCapabilities:
    """What an `(provider, model)` pair can actually do.

    Any field set to ``None`` means *unknown* — the engine should treat that
    as "don't claim this works." A known-False is stronger: route around it
    or refuse upfront with a clear error. ``probed=True`` means ``validate()``
    actually exercised the capability against the live endpoint, not just
    looked it up in ``KNOWN_MODELS``.
    """

    provider: str
    model: str
    tools: Optional[bool] = None
    vision: Optional[bool] = None
    streaming: bool = True
    context_window: Optional[int] = None
    notes: List[str] = field(default_factory=list)
    probed: bool = False

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "tools": self.tools,
            "vision": self.vision,
            "streaming": self.streaming,
            "context_window": self.context_window,
            "notes": list(self.notes),
            "probed": self.probed,
        }


def _lookup_known_model(model: str) -> Dict[str, object]:
    """Return the KNOWN_MODELS entry for ``model``, or an empty dict."""
    if not model:
        return {}
    return KNOWN_MODELS.get(model, {})


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

    def supports_native_tools(self) -> bool:
        """Whether this provider implements `complete_with_tools`."""
        return False

    def capabilities(self) -> ProviderCapabilities:
        """What this (provider, model) pair can do.

        Default is conservative: tools/vision unknown, streaming True (because
        the base class ships a non-streaming fallback that emits one delta).
        Each concrete provider should override to declare what its endpoint
        actually supports, ideally consulting :data:`KNOWN_MODELS` for the
        currently-configured model.
        """
        model = getattr(self, "QUALITY_MODEL", "") or ""
        provider_name = self.__class__.__name__.replace("Provider", "").lower()
        return ProviderCapabilities(
            provider=provider_name,
            model=model,
            tools=None,
            vision=None,
            streaming=True,
        )

    def probe_capabilities(self) -> ProviderCapabilities:
        """Exercise the live endpoint to confirm declared capabilities.

        Default returns ``capabilities()`` with ``probed=False`` — providers
        that can do a cheap tool-call ping (Anthropic, OpenAI, OpenAI-compat)
        should override and set ``probed=True`` after a successful probe.
        Used by ``/api/ai/validate-key`` so the settings UI can show real
        green/red indicators instead of guesses.
        """
        return self.capabilities()

    def complete_with_tools(
        self,
        messages: list,
        tools: list,
        max_tokens: int,
        model: str,
        system: str | None = None,
        documents: list | None = None,
        extended_thinking: bool = False,
    ) -> dict:
        """Run one turn of a tool-calling conversation.

        `messages` is the ongoing message list in each provider's native shape
        (appended to across the loop). `tools` is the provider-native tool list.
        `documents`, when supplied, carries Anthropic native-citation document
        blocks; providers without native citations ignore it (the engine keeps
        prose context in the system prompt for those paths).
        `extended_thinking`, when true on a thinking-capable provider+model,
        asks the API to allocate a reasoning budget before the visible answer
        — the engine surfaces the resulting reasoning trace via ``thinking``
        events. Providers without extended-thinking support ignore it.

        Returns a dict:
          {
            "stop_reason": "tool_use" | "end_turn" | "stop",
            "text": str (concatenated assistant text; may be "" when only tool_use),
            "tool_calls": [{"id": str, "name": str, "input": dict}, ...],
            "citations": [{...}, ...]  (optional — only providers with native
                                        citation support populate this),
            "thinking": str (optional — concatenated extended-thinking text;
                             empty when extended_thinking is off or unsupported),
            "assistant_message": dict (provider-native message to append verbatim),
            "usage": {"input_tokens": int, "output_tokens": int, "model": str},
          }
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support native tool calling")

    def stream_chat(
        self,
        messages: list,
        max_tokens: int,
        model: str,
        system: str | None = None,
        documents: list | None = None,
        extended_thinking: bool = False,
    ) -> Iterator[dict]:
        """Stream a chat completion (no tools), yielding event dicts.

        Used by AgenticEngine for the final-answer pass after the tool loop
        terminates. Yields events of three shapes:
          {"delta": str}              — one fragment of the assistant text
          {"citation": {...}}         — one structured citation (Anthropic native
                                        citations only; other providers do not emit)
          {"thinking": str}           — one fragment of extended-thinking text
                                        (Anthropic thinking-capable models only)
          {"done": True, "usage": {"input_tokens": int, "output_tokens": int, "model": str}}

        Default implementation falls back to ``complete_with_tools`` with no
        tools and emits the entire text as a single delta — providers should
        override for real per-token streaming.
        """
        result = self.complete_with_tools(messages, [], max_tokens, model, system)
        text = (result.get("text") or "").strip()
        if text:
            yield {"delta": text}
        yield {"done": True, "usage": result.get("usage") or {"input_tokens": 0, "output_tokens": 0, "model": model}}

    def stream(
        self,
        prompt: str,
        max_tokens: int,
        model: str,
    ) -> Iterator[dict]:
        """Stream a single-prompt completion, yielding event dicts.

        Used by OneShotEngine (/notes, /followup, /ask). Same event shape as
        ``stream_chat``. Default falls back to ``complete`` and emits the
        result as a single delta — providers should override for real
        per-token streaming.
        """
        result = self.complete(prompt, max_tokens, model)
        text = (result.get("text") or "").strip()
        if text:
            yield {"delta": text}
        yield {"done": True, "usage": result.get("usage") or {"input_tokens": 0, "output_tokens": 0, "model": model}}


class AnthropicProvider(AIProvider):
    """Anthropic Claude provider."""

    FAST_MODEL = "claude-haiku-4-5-20251001"
    QUALITY_MODEL = "claude-sonnet-4-6"
    # Reserved for long-context paths that earn the per-token premium; falls
    # back to QUALITY_MODEL when the account lacks Opus access.
    OPUS_MODEL = "claude-opus-4-7"

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

    def supports_native_tools(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        info = _lookup_known_model(self.QUALITY_MODEL)
        notes: list[str] = []
        if not info:
            notes.append(
                f"Model '{self.QUALITY_MODEL}' is not in KNOWN_MODELS; "
                "Anthropic models generally support tools + vision, but "
                "context_window is unknown."
            )
        return ProviderCapabilities(
            provider="anthropic",
            model=self.QUALITY_MODEL,
            tools=bool(info.get("tools", True)),
            vision=bool(info.get("vision", True)),
            streaming=True,
            context_window=info.get("context_window"),
            notes=notes,
        )

    def probe_capabilities(self) -> ProviderCapabilities:
        caps = self.capabilities()
        try:
            self.client.messages.create(
                model=self.QUALITY_MODEL,
                max_tokens=64,
                tools=[{
                    "name": "ping",
                    "description": "Reply by calling this tool with no args.",
                    "input_schema": {"type": "object", "properties": {}, "required": []},
                }],
                messages=[{"role": "user", "content": "Call the ping tool."}],
            )
            caps.tools = True
            caps.probed = True
        except Exception as e:
            logger.warning("Anthropic tool probe failed for %s: %s", self.QUALITY_MODEL, e)
            caps.tools = False
            caps.probed = True
            caps.notes.append(f"Tool probe failed: {e}")
        return caps

    def _build_system_param(self, system):
        """Wrap system prompt with an ephemeral cache_control breakpoint.

        Render order is tools → system → messages, so a marker on the last
        system block caches tools + system together. Within a single chat
        turn, every tool-loop iteration reuses the same system prompt and
        reads the cache; across turns, identical questions read it too.
        Different questions in a multi-turn chat will not hit because the
        retrieved-context block embedded in the system prompt varies — that
        is a known limit, addressed by moving retrieved context into the
        user turn (follow-up).
        """
        if not system:
            return None
        if isinstance(system, list):
            return system
        return [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]

    def _extract_usage(self, raw_usage, model: str) -> dict:
        usage = {
            "input_tokens": getattr(raw_usage, "input_tokens", 0),
            "output_tokens": getattr(raw_usage, "output_tokens", 0),
            "cache_creation_input_tokens": getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
            "model": model,
        }
        from services.diagnostics import record_cache_usage
        record_cache_usage(
            input_tokens=usage["input_tokens"],
            cache_creation_input_tokens=usage["cache_creation_input_tokens"],
            cache_read_input_tokens=usage["cache_read_input_tokens"],
        )
        return usage

    def _inject_documents(self, messages, documents):
        """Prepend ``documents`` content blocks to the first user message.

        Anthropic's native Citations API expects retrieved context as
        ``{"type": "document", source, title, citations: {enabled: True}}``
        blocks on a user turn — putting them on the first user message keeps
        the system-prompt cache breakpoint intact (system precedes messages
        in the render order, so message-level changes don't invalidate it).
        """
        if not documents:
            return messages
        new_messages = list(messages)
        for i, m in enumerate(new_messages):
            if m.get("role") != "user":
                continue
            existing = m.get("content", "")
            if isinstance(existing, str):
                existing_blocks = [{"type": "text", "text": existing}] if existing else []
            else:
                existing_blocks = list(existing)
            new_messages[i] = {
                "role": "user",
                "content": list(documents) + existing_blocks,
            }
            return new_messages
        return new_messages

    # Default reasoning budget when extended_thinking is enabled. Big enough to
    # earn its keep on TLH wash-sale checks and rebalance trade ordering, small
    # enough that an accidental trivial-question opt-in doesn't 10x the bill.
    THINKING_BUDGET_TOKENS = 8000

    def _supports_thinking(self, model: str) -> bool:
        """Resolve ``thinking`` capability from KNOWN_MODELS; default False
        for unrecognised models so an unknown ID never gets thinking enabled
        silently (Anthropic 422s when the model can't reason)."""
        info = _lookup_known_model(model)
        return bool(info.get("thinking", False))

    def _apply_thinking(self, kwargs: dict, model: str, extended_thinking: bool) -> bool:
        """Mutate ``kwargs`` in place to enable extended thinking when the
        caller asked and the model supports it. Returns True iff thinking
        was actually turned on so callers can adjust downstream parsing.

        ``max_tokens`` must exceed ``budget_tokens`` (the budget counts toward
        the output cap), so we bump it when the caller's max would leave no
        room for a visible answer.
        """
        if not extended_thinking or not self._supports_thinking(model):
            return False
        budget = self.THINKING_BUDGET_TOKENS
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
        if kwargs.get("max_tokens", 0) <= budget:
            kwargs["max_tokens"] = budget + 2048
        # Extended thinking forces temperature=1; we don't set temperature
        # anywhere else, so no override needed.
        return True

    def complete_with_tools(self, messages, tools, max_tokens, model, system=None, documents=None, extended_thinking=False):
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._inject_documents(messages, documents),
            "tools": tools,
        }
        sys_param = self._build_system_param(system)
        if sys_param is not None:
            kwargs["system"] = sys_param
        thinking_on = self._apply_thinking(kwargs, model, extended_thinking)
        response = self.client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        citations: list[dict] = []
        thinking_parts: list[str] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
                for c in getattr(block, "citations", None) or []:
                    parsed = self._parse_citation_block(c)
                    if parsed is not None:
                        citations.append(parsed)
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": dict(block.input or {}),
                })
            elif btype in ("thinking", "redacted_thinking"):
                # Visible reasoning trace; redacted blocks have no `.thinking`
                # attribute but must still round-trip on replay.
                t = getattr(block, "thinking", None)
                if t:
                    thinking_parts.append(t)
        # Anthropic expects extended-thinking blocks back on replay verbatim
        # (with the ``signature`` field) so the model can resume from its
        # earlier reasoning. Round-trip thinking + redacted_thinking blocks
        # in addition to text + tool_use, in original order.
        assistant_content = []
        for b in response.content:
            btype = getattr(b, "type", None)
            if btype == "text":
                assistant_content.append({"type": "text", "text": b.text})
            elif btype == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": dict(b.input or {}),
                })
            elif btype == "thinking":
                block_dict: dict = {"type": "thinking", "thinking": getattr(b, "thinking", "")}
                sig = getattr(b, "signature", None)
                if sig is not None:
                    block_dict["signature"] = sig
                assistant_content.append(block_dict)
            elif btype == "redacted_thinking":
                assistant_content.append({
                    "type": "redacted_thinking",
                    "data": getattr(b, "data", ""),
                })
        assistant_message = {"role": "assistant", "content": assistant_content}
        return {
            "stop_reason": response.stop_reason,
            "text": "".join(text_parts).strip(),
            "tool_calls": tool_calls,
            "citations": citations,
            "thinking": "".join(thinking_parts).strip() if thinking_on else "",
            "assistant_message": assistant_message,
            "usage": self._extract_usage(response.usage, model),
        }

    @staticmethod
    def _parse_citation_block(c) -> Optional[dict]:
        """Normalize an Anthropic citation object into a plain dict.

        Plain-text documents emit ``char_location`` citations; we ignore page
        and content-block variants for now since Finn passes chunks as text.
        """
        ctype = getattr(c, "type", None)
        if ctype != "char_location":
            return None
        return {
            "type": "char_location",
            "document_index": getattr(c, "document_index", None),
            "document_title": getattr(c, "document_title", None),
            "cited_text": getattr(c, "cited_text", None),
            "start_char_index": getattr(c, "start_char_index", None),
            "end_char_index": getattr(c, "end_char_index", None),
        }

    def stream_chat(self, messages, max_tokens, model, system=None, documents=None, extended_thinking=False):
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._inject_documents(messages, documents),
        }
        sys_param = self._build_system_param(system)
        if sys_param is not None:
            kwargs["system"] = sys_param
        self._apply_thinking(kwargs, model, extended_thinking)

        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "model": model,
        }
        with self.client.messages.stream(**kwargs) as stream:
            # Iterate the raw event stream so citation_delta events surface
            # alongside text deltas. text_stream alone drops them.
            for event in stream:
                etype = getattr(event, "type", None)
                if etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    dtype = getattr(delta, "type", None) if delta is not None else None
                    if dtype == "text_delta":
                        text = getattr(delta, "text", "") or ""
                        if text:
                            yield {"delta": text}
                    elif dtype == "citations_delta":
                        citation = getattr(delta, "citation", None)
                        parsed = self._parse_citation_block(citation) if citation is not None else None
                        if parsed is not None:
                            yield {"citation": parsed}
                    elif dtype == "thinking_delta":
                        # Reasoning trace fragments — surface separately so the
                        # engine can route them to ``thinking`` SSE events
                        # instead of the visible ``text_delta`` stream.
                        thinking_text = getattr(delta, "thinking", "") or ""
                        if thinking_text:
                            yield {"thinking": thinking_text}
            try:
                final = stream.get_final_message()
                usage = self._extract_usage(final.usage, model)
            except Exception as e:
                logger.warning("Anthropic stream final-message read failed: %s", e)
        yield {"done": True, "usage": usage}

    def stream(self, prompt, max_tokens, model):
        yield from self.stream_chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            model=model,
        )

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

    def __init__(self, api_key: str, model: Optional[str] = None, base_url: Optional[str] = None):
        from openai import OpenAI
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._base_url = base_url
        self.client = OpenAI(**client_kwargs)
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
        vision_kwargs: dict = {"api_key": self.client.api_key, "timeout": 120.0}
        if getattr(self, '_base_url', None):
            vision_kwargs["base_url"] = self._base_url
        vision_client = OpenAI(**vision_kwargs)
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

    def supports_native_tools(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        info = _lookup_known_model(self.QUALITY_MODEL)
        provider_name = self.__class__.__name__.replace("Provider", "").lower()
        notes: list[str] = []
        if not info:
            notes.append(
                f"Model '{self.QUALITY_MODEL}' is not in KNOWN_MODELS; "
                "tool calling and vision support assumed but unverified. "
                "Run validation to probe."
            )
        return ProviderCapabilities(
            provider=provider_name,
            model=self.QUALITY_MODEL,
            # Default to True for known OpenAI-compat endpoints with tool calling;
            # subclasses (e.g. OpenAICompatible) override when the model is
            # genuinely unknown.
            tools=bool(info["tools"]) if "tools" in info else True,
            vision=bool(info["vision"]) if "vision" in info else None,
            streaming=True,
            context_window=info.get("context_window"),
            notes=notes,
        )

    def probe_capabilities(self) -> ProviderCapabilities:
        caps = self.capabilities()
        try:
            self.client.chat.completions.create(
                model=self.QUALITY_MODEL,
                max_tokens=64,
                messages=[{"role": "user", "content": "Call the ping tool."}],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "ping",
                        "description": "Reply by calling this tool with no args.",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }],
            )
            caps.tools = True
            caps.probed = True
        except Exception as e:
            logger.warning("OpenAI-compat tool probe failed for %s: %s", self.QUALITY_MODEL, e)
            caps.tools = False
            caps.probed = True
            caps.notes.append(f"Tool probe failed: {e}")
        return caps

    def complete_with_tools(self, messages, tools, max_tokens, model, system=None):
        import json as _json
        payload_messages = list(messages)
        if system and (not payload_messages or payload_messages[0].get("role") != "system"):
            payload_messages = [{"role": "system", "content": system}] + payload_messages
        response = self.client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=payload_messages,
            tools=tools,
        )
        choice = response.choices[0]
        message = choice.message
        text = (message.content or "").strip()
        tool_calls_raw = getattr(message, "tool_calls", None) or []
        tool_calls: list[dict] = []
        for tc in tool_calls_raw:
            args_str = tc.function.arguments or "{}"
            try:
                args = _json.loads(args_str)
            except Exception:
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "input": args})

        # Rebuild the assistant message in OpenAI's native shape so it can be
        # appended back for the next turn.
        assistant_message: dict = {"role": "assistant", "content": message.content}
        if tool_calls_raw:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                }
                for tc in tool_calls_raw
            ]

        stop_map = {"tool_calls": "tool_use", "stop": "end_turn", "length": "end_turn"}
        return {
            "stop_reason": stop_map.get(choice.finish_reason, choice.finish_reason),
            "text": text,
            "tool_calls": tool_calls,
            "assistant_message": assistant_message,
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "model": model,
            },
        }

    def stream_chat(self, messages, max_tokens, model, system=None):
        payload_messages = list(messages)
        if system and (not payload_messages or payload_messages[0].get("role") != "system"):
            payload_messages = [{"role": "system", "content": system}] + payload_messages

        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": payload_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            stream = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            # Some OpenAI-compatible providers reject stream_options. Retry without it.
            logger.warning("Streaming with usage failed (%s); retrying without stream_options", e)
            kwargs.pop("stream_options", None)
            stream = self.client.chat.completions.create(**kwargs)

        usage = {"input_tokens": 0, "output_tokens": 0, "model": model}
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if choices:
                delta_obj = getattr(choices[0], "delta", None)
                delta = getattr(delta_obj, "content", None) if delta_obj else None
                if delta:
                    yield {"delta": delta}
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = {
                    "input_tokens": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(chunk_usage, "completion_tokens", 0) or 0,
                    "model": model,
                }
        yield {"done": True, "usage": usage}

    def stream(self, prompt, max_tokens, model):
        yield from self.stream_chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            model=model,
        )

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

    def capabilities(self) -> ProviderCapabilities:
        info = _lookup_known_model(self.model)
        # Local Ollama drops to the ReAct text loop in services/chat/engine.py
        # (see _run_react_fallback). Native tool calling is not wired in for
        # this provider — declare it explicitly so the UI can warn the user
        # that chat will be slower and less reliable than Anthropic/OpenAI.
        vision_likely = any(tag in self.model.lower() for tag in ("llava", "vision", "bakllava", "moondream"))
        return ProviderCapabilities(
            provider="ollama",
            model=self.model,
            tools=False,
            vision=vision_likely or bool(info.get("vision", False)),
            streaming=True,
            context_window=info.get("context_window"),
            notes=[
                "Finn uses ReAct fallback for local Ollama — tool calls "
                "happen inside a prose loop, not the model's native tool schema. "
                "Use Anthropic, OpenAI, or Ollama Cloud for richer tool use."
            ],
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

    def stream(self, prompt, max_tokens, model):
        """Stream a single-prompt completion from Ollama's native /api/chat."""
        import httpx

        usage = {"input_tokens": 0, "output_tokens": 0, "model": model}
        try:
            with httpx.Client(timeout=300.0) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": True,
                        "options": {"num_predict": max_tokens},
                    },
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        delta = (data.get("message") or {}).get("content") or ""
                        if delta:
                            yield {"delta": delta}
                        if data.get("done"):
                            usage = {
                                "input_tokens": data.get("prompt_eval_count", 0),
                                "output_tokens": data.get("eval_count", 0),
                                "model": model,
                            }
        except Exception as e:
            logger.warning("Ollama streaming failed (%s); falling back to non-streaming", e)
            result = self.complete(prompt=prompt, max_tokens=max_tokens, model=model)
            text = (result.get("text") or "").strip()
            if text:
                yield {"delta": text}
            usage = result.get("usage") or usage
        yield {"done": True, "usage": usage}

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


class GrokProvider(OpenAIProvider):
    """Grok (xAI) provider — OpenAI-compatible API."""

    FAST_MODEL = "grok-3-mini"
    QUALITY_MODEL = "grok-3"

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__(api_key, model=model, base_url="https://api.x.ai/v1")

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
            logger.warning("Grok authentication failed")
            return False
        except RateLimitError:
            return True
        except Exception as e:
            logger.error(f"Grok validation error: {e}")
            raise


class GoogleProvider(OpenAIProvider):
    """Google Gemini provider via the OpenAI-compatible endpoint."""

    FAST_MODEL = "gemini-2.0-flash"
    QUALITY_MODEL = "gemini-2.5-pro-preview-03-25"

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__(
            api_key,
            model=model,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

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
            logger.warning("Google authentication failed")
            return False
        except RateLimitError:
            return True
        except Exception as e:
            logger.error(f"Google validation error: {e}")
            raise


class GitHubProvider(OpenAIProvider):
    """GitHub Models provider — OpenAI-compatible API.

    Lets users access models hosted on the GitHub Models marketplace
    (gpt-4o, Llama, Phi, Mistral, DeepSeek, etc.) via a GitHub Personal
    Access Token. Available to anyone with a GitHub account; rate limits
    are higher for paid Copilot subscribers.

    Auth: GitHub PAT with the `models:read` scope.
    Endpoint: https://models.github.ai/inference (OpenAI-compatible).
    Model IDs are namespaced like `openai/gpt-4o`, `meta/Llama-3.3-70B-Instruct`.
    """

    FAST_MODEL = "openai/gpt-4o-mini"
    QUALITY_MODEL = "openai/gpt-4o"

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__(
            api_key,
            model=model,
            base_url="https://models.github.ai/inference",
        )

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
            logger.warning("GitHub Models authentication failed")
            return False
        except RateLimitError:
            return True
        except Exception as e:
            logger.error(f"GitHub Models validation error: {e}")
            raise


class OllamaCloudProvider(OpenAIProvider):
    """Ollama Cloud provider — hosted OSS models via OpenAI-compatible API.

    Auth: API key from ollama.com (Bearer token; OpenAI SDK handles this).
    Endpoint: https://ollama.com/v1 (OpenAI-compatible chat completions).
    Model tags use plain names (e.g. `gpt-oss:120b`, `gemma4:31b`) — the
    `:cloud` suffix is only used when routing through a local Ollama daemon.
    Supports native function calling and vision (inherited from OpenAIProvider).
    """

    FAST_MODEL = "gpt-oss:20b"
    QUALITY_MODEL = "gpt-oss:120b"

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__(api_key, model=model, base_url="https://ollama.com/v1")

    def validate(self) -> bool:
        from openai import AuthenticationError, RateLimitError, APIConnectionError
        try:
            self.client.chat.completions.create(
                model=self.FAST_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except AuthenticationError:
            logger.warning("Ollama Cloud authentication failed — check your API key at ollama.com/settings/keys")
            return False
        except RateLimitError:
            return True
        except APIConnectionError as e:
            logger.error("Ollama Cloud connection failed: %s", e)
            raise ConnectionError("Could not reach ollama.com. Check your network connection and try again.") from e
        except Exception as e:
            logger.error("Ollama Cloud validation error: %s", e)
            raise


class OpenAICompatibleProvider(OpenAIProvider):
    """Generic OpenAI-compatible provider for custom endpoints (vLLM, LM Studio, Groq, etc.)."""

    def __init__(self, api_key: str, base_url: str, model: str = "default"):
        super().__init__(api_key or "none", model=model, base_url=base_url)
        self.FAST_MODEL = model
        self.QUALITY_MODEL = model

    def capabilities(self) -> ProviderCapabilities:
        # Caller pointed us at an unknown URL with an unknown model. Don't
        # claim anything we can't back up — let validate() probe.
        info = _lookup_known_model(self.QUALITY_MODEL)
        return ProviderCapabilities(
            provider="openai_compatible",
            model=self.QUALITY_MODEL,
            tools=bool(info["tools"]) if "tools" in info else None,
            vision=bool(info["vision"]) if "vision" in info else None,
            streaming=True,
            context_window=info.get("context_window"),
            notes=[
                f"Custom endpoint at {self._base_url}. Capabilities are "
                "unknown until validation probes them — small/older models "
                "behind OpenAI-compatible APIs often reject tool calls."
            ],
        )

    def validate(self) -> bool:
        try:
            self.client.chat.completions.create(
                model=self.FAST_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except Exception as e:
            logger.error(f"OpenAI-compatible validation error: {e}")
            return False


def create_provider(provider_name: str, api_key: str = None, **kwargs) -> AIProvider:
    """Create an AI provider instance.

    Args:
        provider_name: Provider name — anthropic, openai, grok, google, github, openai_compatible, ollama, ollama_cloud
        api_key: API key for cloud providers (not needed for local Ollama or key-less endpoints)
        **kwargs:
            model       – model name override
            base_url    – custom base URL (for openai, openai_compatible, ollama)
    """
    if provider_name == "anthropic":
        if not api_key:
            raise ValueError("API key required for Anthropic")
        return AnthropicProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "openai":
        if not api_key:
            raise ValueError("API key required for OpenAI")
        return OpenAIProvider(api_key, model=kwargs.get("model"), base_url=kwargs.get("base_url"))
    elif provider_name == "grok":
        if not api_key:
            raise ValueError("API key required for Grok")
        return GrokProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "google":
        if not api_key:
            raise ValueError("API key required for Google")
        return GoogleProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "github":
        if not api_key:
            raise ValueError("API key required for GitHub Models (GitHub PAT with models:read scope)")
        return GitHubProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "openai_compatible":
        base_url = kwargs.get("base_url")
        if not base_url:
            raise ValueError("base_url required for openai_compatible provider")
        return OpenAICompatibleProvider(
            api_key or "none",
            base_url=base_url,
            model=kwargs.get("model", "default"),
        )
    elif provider_name == "ollama":
        return OllamaProvider(
            base_url=kwargs.get("base_url", "http://localhost:11434"),
            model=kwargs.get("model", "llama3.2"),
        )
    elif provider_name == "ollama_cloud":
        if not api_key:
            raise ValueError("API key required for Ollama Cloud (get one at https://ollama.com)")
        return OllamaCloudProvider(api_key, model=kwargs.get("model"))
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


def _parse_followup_lines(text: str, max_questions: int) -> List[str]:
    """Clean a model's newline-delimited follow-up output into a short list.

    Strips list markers ("- ", "* ", "1. ", "2) "), surrounding quotes, an
    echoed "Follow-up questions:" header, and case-insensitive duplicates.
    Caps the result at ``max_questions``.
    """
    out: List[str] = []
    seen: set = set()
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        # Strip a leading bullet or "<n>." / "<n>)" enumerator.
        if s[:2] in ("- ", "* ", "• "):
            s = s[2:].strip()
        else:
            i = 0
            while i < len(s) and s[i].isdigit():
                i += 1
            if 0 < i < len(s) and s[i] in ".)" and s[i + 1:i + 2] == " ":
                s = s[i + 2:].strip()
        s = s.strip('"').strip("'").strip()
        if not s:
            continue
        if s.lower().rstrip(":") in (
            "follow-up questions", "followup questions", "related", "questions",
        ):
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_questions:
            break
    return out


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

    def suggest_followups(
        self,
        question: str,
        answer: str,
        *,
        kind: str = "general",
        max_questions: int = 3,
    ) -> List[str]:
        """Suggest follow-up questions the user is most likely to ask next.

        Perplexity-style "Related" prompts: after an answer lands, surface a
        few next questions so the user can keep pulling the thread without
        composing from scratch. Runs on ``fast_model`` (Haiku 4.5 on
        Anthropic) because these are throwaway suggestions, not analysis.

        Returns ``[]`` on empty input or any failure — the feature is purely
        additive and must never break or delay a chat turn.

        For ``financial``/``mixed`` collections the questions keep Finn's
        advisor framing (third person about *the client's* portfolio), so a
        clicked suggestion reads naturally when it becomes the next message.
        """
        if not (answer or "").strip():
            return []

        if kind in ("financial", "mixed"):
            framing_rule = (
                "- The user is a financial advisor analyzing a CLIENT's portfolio. "
                'Phrase each question in the third person about "the client\'s" '
                'holdings, accounts, or positions — never "your portfolio".\n'
            )
        else:
            framing_rule = ""

        prompt = (
            "Suggest the most useful follow-up questions the user might ask "
            "next, based on the question and answer below.\n\n"
            "Rules:\n"
            f"- Output at most {max_questions} questions, one per line.\n"
            "- No numbering, no bullets, no preamble — just the questions, each "
            "on its own line.\n"
            "- Each must be self-contained (resolve pronouns and references) and "
            "answerable from the same documents or data.\n"
            "- Keep each under 12 words.\n"
            "- Suggest genuine next steps, not rephrasings of the original "
            "question.\n"
            f"{framing_rule}"
            "\n"
            f"Question: {question}\n"
            f"Answer: {answer[:2000]}\n\n"
            "Follow-up questions:"
        )

        try:
            response = self.provider.complete(prompt, max_tokens=200, model=self.fast_model)
        except Exception as e:
            logger.warning(f"Follow-up suggestion failed: {e}")
            return []

        return _parse_followup_lines(response.get("text", ""), max_questions)

    def synthesize_results(
        self,
        query: str,
        results: List[dict],
        collection_overview: Optional[str] = None,
        structured_context: Optional[str] = None,
    ) -> dict:
        """Generate a coherent answer from search results with citations.

        This is the highest-value AI feature: it turns "here are 10 document
        chunks" into "here is the answer to your question, with sources."

        If `collection_overview` is provided, it is injected into the prompt so
        the model can also answer meta-questions about the collection itself
        (file counts, available documents, date ranges) that aren't answerable
        from the retrieved chunks alone.

        If `structured_context` is provided, it contains full JSONL dumps of
        small CSV/XLSX tables in scope — the model must treat that as the
        authoritative source for any numeric/aggregation question about those
        files, since top-K chunk retrieval would silently truncate the data.
        """
        if not results and not collection_overview and not structured_context:
            return {"synthesis": "", "usage": None}

        context_parts = []
        for i, r in enumerate(results):
            context_parts.append(
                f"[Source {i + 1}: {r['filename']}, page {r['page_number']}]\n"
                f"{r['text_snippet']}"
            )
        context = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant passages found."

        overview_section = (
            f"COLLECTION OVERVIEW:\n{collection_overview}\n\n"
            if collection_overview
            else ""
        )
        structured_section = (
            f"{structured_context}\n\n"
            if structured_context
            else ""
        )

        prompt = (
            "You are a research assistant. Based on the information below, "
            "provide a clear, concise answer to the user's query. Cite your sources "
            "using [Source N] notation when referencing retrieved passages.\n\n"
            "Rules:\n"
            "- Use the COLLECTION OVERVIEW for meta-questions about the knowledge base itself "
            "(file counts, available documents, date ranges, what's in the collection).\n"
            "- If STRUCTURED TABLES are provided, those JSONL dumps are the complete and "
            "authoritative contents of the listed CSV/XLSX files. For any numeric, "
            "aggregation, sum, count, average, filter, date-range, or ranking question "
            "about those files, answer EXCLUSIVELY from the JSONL rows — every row is "
            "present, do not estimate from chunks and do not assume data is missing. "
            "Show your arithmetic when summing so the user can verify.\n"
            "- Use the RETRIEVED SOURCES for questions about prose/document content, and cite "
            "specific sources with [Source N].\n"
            "- If none of the sections contain enough info, say so.\n"
            "- Be concise but thorough.\n"
            "- Use plain language.\n\n"
            f"{overview_section}"
            f"{structured_section}"
            f"Query: {query}\n\n"
            f"RETRIEVED SOURCES:\n{context}"
        )

        start = time.time()
        response = self.provider.complete(prompt, max_tokens=1024, model=self.quality_model)
        elapsed = time.time() - start

        logger.info(f"Synthesized answer in {elapsed:.2f}s ({len(response['text'])} chars)")

        return {
            "synthesis": response["text"],
            "usage": response["usage"],
        }
