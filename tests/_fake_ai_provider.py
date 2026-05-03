"""FakeAIProvider for chat-engine unit tests.

Drives ``services.chat.engine`` without hitting a real LLM. Each provider
method (``complete``, ``complete_with_tools``, ``stream``, ``stream_chat``)
reads from a FIFO queue you pre-load on the fixture, and records every call
on a paired list so tests can assert ordering and arguments.

Example
-------
    fake = FakeAIProvider()
    fake.queue_tools_response(tool_calls=[{"id": "1", "name": "list_tables", "input": {}}])
    fake.queue_stream_chat_chunks(["Hello", " world"])
    # ... drive run_agentic and assert events.
"""

from __future__ import annotations

from typing import Any, Iterator

from services.ai_service import AIProvider


class FakeAIProvider(AIProvider):
    """Scriptable AIProvider for engine tests."""

    FAST_MODEL = "fake-fast"
    QUALITY_MODEL = "fake-quality"

    def __init__(self, *, native_tools: bool = True):
        self._native = native_tools
        self.tools_responses: list[dict[str, Any]] = []
        self.complete_responses: list[dict[str, Any]] = []
        self.stream_chat_responses: list[list[str] | Exception] = []
        self.stream_responses: list[list[str] | Exception] = []
        self.tools_calls: list[dict[str, Any]] = []
        self.complete_calls: list[dict[str, Any]] = []
        self.stream_chat_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    # -- queueing helpers -------------------------------------------------

    def queue_tools_response(
        self,
        *,
        text: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        stop_reason: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Queue a complete_with_tools response.

        ``tool_calls`` shape matches what AnthropicProvider/OpenAIProvider return:
        ``[{"id": str, "name": str, "input": dict}]``. When non-empty the
        ``stop_reason`` defaults to ``tool_use``; otherwise ``end_turn``.
        """
        tcs = tool_calls or []
        self.tools_responses.append({
            "stop_reason": stop_reason or ("tool_use" if tcs else "end_turn"),
            "text": text,
            "tool_calls": tcs,
            "assistant_message": {"role": "assistant", "content": text or ""},
            "usage": usage or {"input_tokens": 1, "output_tokens": 1, "model": self.FAST_MODEL},
        })

    def queue_complete_response(
        self,
        text: str = "",
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.complete_responses.append({
            "text": text,
            "usage": usage or {"input_tokens": 1, "output_tokens": 1, "model": self.FAST_MODEL},
        })

    def queue_stream_chat_chunks(self, chunks: list[str]) -> None:
        self.stream_chat_responses.append(list(chunks))

    def queue_stream_chat_error(self, exc: Exception) -> None:
        self.stream_chat_responses.append(exc)

    def queue_stream_chunks(self, chunks: list[str]) -> None:
        self.stream_responses.append(list(chunks))

    def queue_stream_error(self, exc: Exception) -> None:
        self.stream_responses.append(exc)

    # -- AIProvider implementation ---------------------------------------

    def supports_native_tools(self) -> bool:
        return self._native

    def validate(self) -> bool:
        return True

    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        self.complete_calls.append({"prompt": prompt, "max_tokens": max_tokens, "model": model})
        if not self.complete_responses:
            return {"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "model": model}}
        return self.complete_responses.pop(0)

    def complete_with_tools(self, messages, tools, max_tokens, model, system=None):
        self.tools_calls.append({
            "messages": list(messages),
            "tools": tools,
            "max_tokens": max_tokens,
            "model": model,
            "system": system,
        })
        if not self.tools_responses:
            raise RuntimeError("FakeAIProvider exhausted tools_responses queue")
        return self.tools_responses.pop(0)

    def stream_chat(self, messages, max_tokens, model, system=None) -> Iterator[dict]:
        self.stream_chat_calls.append({
            "messages": list(messages),
            "max_tokens": max_tokens,
            "model": model,
            "system": system,
        })
        if not self.stream_chat_responses:
            yield {"done": True, "usage": {"input_tokens": 0, "output_tokens": 0, "model": model}}
            return
        chunks = self.stream_chat_responses.pop(0)
        if isinstance(chunks, Exception):
            raise chunks
        for chunk in chunks:
            yield {"delta": chunk}
        yield {"done": True, "usage": {"input_tokens": 1, "output_tokens": len(chunks), "model": model}}

    def stream(self, prompt, max_tokens, model) -> Iterator[dict]:
        self.stream_calls.append({"prompt": prompt, "max_tokens": max_tokens, "model": model})
        if not self.stream_responses:
            yield {"done": True, "usage": {"input_tokens": 0, "output_tokens": 0, "model": model}}
            return
        chunks = self.stream_responses.pop(0)
        if isinstance(chunks, Exception):
            raise chunks
        for chunk in chunks:
            yield {"delta": chunk}
        yield {"done": True, "usage": {"input_tokens": 1, "output_tokens": len(chunks), "model": model}}
