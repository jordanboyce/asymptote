"""Extended-thinking coverage for AnthropicProvider + the chat engine.

Verifies that the per-turn ``extended_thinking`` opt-in:

  * adds ``thinking={"type": "enabled", "budget_tokens": ...}`` to the
    Anthropic SDK call only when the configured model is thinking-capable,
  * bumps ``max_tokens`` so the visible answer still has room when the
    caller's budget would have been swallowed by the reasoning trace,
  * round-trips ``thinking`` and ``redacted_thinking`` content blocks back
    into ``assistant_message`` so the model can resume reasoning on
    subsequent tool-loop iterations,
  * surfaces ``thinking_delta`` stream events as ``{"thinking": ...}``
    provider events,
  * is silently ignored on non-Anthropic providers (the engine doesn't
    pass the kwarg, so FakeAIProvider keeps its narrow signature).

The Anthropic SDK is mocked — no network calls.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

from services.ai_service import AnthropicProvider
from services.diagnostics import get_buffer


def _drain(agen: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    async def _go():
        out: list[dict[str, Any]] = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_go())


def _make_provider(client_mock):
    p = AnthropicProvider.__new__(AnthropicProvider)
    p.client = client_mock
    p.FAST_MODEL = "claude-haiku-4-5-20251001"
    p.QUALITY_MODEL = "claude-sonnet-4-6"
    p.OPUS_MODEL = "claude-opus-4-7"
    return p


def _msg_response(*, content=None, cache_creation=0, cache_read=0):
    return SimpleNamespace(
        content=content if content is not None else [SimpleNamespace(type="text", text="ok", citations=[])],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        ),
    )


@pytest.fixture(autouse=True)
def _clear_diagnostics():
    get_buffer().clear()
    yield
    get_buffer().clear()


# --- complete_with_tools --------------------------------------------------


def test_thinking_kwarg_added_when_enabled_on_supported_model():
    """Opus 4.7 is thinking-capable in KNOWN_MODELS — opt-in flows through."""
    client = MagicMock()
    client.messages.create.return_value = _msg_response()
    provider = _make_provider(client)

    provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=16000,
        model="claude-opus-4-7",
        system="you are helpful",
        extended_thinking=True,
    )

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 8000}


def test_thinking_kwarg_omitted_when_extended_thinking_false():
    client = MagicMock()
    client.messages.create.return_value = _msg_response()
    provider = _make_provider(client)

    provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=128,
        model="claude-opus-4-7",
        system="you are helpful",
    )

    assert "thinking" not in client.messages.create.call_args.kwargs


def test_thinking_kwarg_omitted_for_unknown_model_even_when_requested():
    """Unknown / unrecognised model IDs must not silently get thinking
    enabled — Anthropic 422s when the model can't reason, and silent failure
    here would mask the misconfiguration."""
    client = MagicMock()
    client.messages.create.return_value = _msg_response()
    provider = _make_provider(client)

    provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=4096,
        model="some-unknown-finetune",
        system="you are helpful",
        extended_thinking=True,
    )

    assert "thinking" not in client.messages.create.call_args.kwargs


def test_max_tokens_bumped_to_leave_room_for_visible_answer():
    """Anthropic requires max_tokens > budget_tokens. When the caller's
    max_tokens is at-or-below the budget, the provider bumps it so the
    answer still has room past the reasoning trace."""
    client = MagicMock()
    client.messages.create.return_value = _msg_response()
    provider = _make_provider(client)

    provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=2048,  # < 8000 budget
        model="claude-opus-4-7",
        system="you are helpful",
        extended_thinking=True,
    )

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["max_tokens"] > 8000


def test_max_tokens_preserved_when_already_above_budget():
    """When the caller already asked for plenty of room, don't second-guess."""
    client = MagicMock()
    client.messages.create.return_value = _msg_response()
    provider = _make_provider(client)

    provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=20000,
        model="claude-opus-4-7",
        system="you are helpful",
        extended_thinking=True,
    )

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["max_tokens"] == 20000


def test_thinking_text_returned_when_enabled():
    """When the response includes thinking content blocks, the parsed
    ``thinking`` field on the returned dict carries the trace."""
    client = MagicMock()
    client.messages.create.return_value = _msg_response(content=[
        SimpleNamespace(type="thinking", thinking="step 1 ... step 2", signature="sig123"),
        SimpleNamespace(type="text", text="answer", citations=[]),
    ])
    provider = _make_provider(client)

    result = provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=16000,
        model="claude-opus-4-7",
        system="you are helpful",
        extended_thinking=True,
    )

    assert result["thinking"] == "step 1 ... step 2"
    assert result["text"] == "answer"


def test_thinking_text_empty_when_extended_thinking_off_even_if_response_has_blocks():
    """Defensive: when the caller didn't ask for thinking, don't surface a
    trace even if the SDK somehow returned thinking blocks. Prevents a
    misconfigured response from leaking reasoning when the caller said no."""
    client = MagicMock()
    client.messages.create.return_value = _msg_response(content=[
        SimpleNamespace(type="thinking", thinking="leaked", signature="x"),
        SimpleNamespace(type="text", text="answer", citations=[]),
    ])
    provider = _make_provider(client)

    result = provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=4096,
        model="claude-opus-4-7",
        system="you are helpful",
        extended_thinking=False,
    )

    assert result["thinking"] == ""


def test_thinking_blocks_round_tripped_into_assistant_message():
    """For multi-iteration tool loops, thinking blocks must come back on
    replay verbatim (with the ``signature`` field) so the model can resume
    its earlier reasoning."""
    client = MagicMock()
    client.messages.create.return_value = _msg_response(content=[
        SimpleNamespace(type="thinking", thinking="reasoning trace", signature="sig-abc"),
        SimpleNamespace(type="text", text="calling tool", citations=[]),
        SimpleNamespace(type="tool_use", id="t1", name="search", input={"q": "x"}),
    ])
    provider = _make_provider(client)

    result = provider.complete_with_tools(
        messages=[{"role": "user", "content": "search for x"}],
        tools=[{"name": "search"}],
        max_tokens=16000,
        model="claude-opus-4-7",
        system="you are helpful",
        extended_thinking=True,
    )

    blocks = result["assistant_message"]["content"]
    types = [b["type"] for b in blocks]
    assert types == ["thinking", "text", "tool_use"]
    thinking_block = blocks[0]
    assert thinking_block["thinking"] == "reasoning trace"
    assert thinking_block["signature"] == "sig-abc"


def test_redacted_thinking_blocks_preserved_in_assistant_message():
    """``redacted_thinking`` blocks have no visible text but must still be
    round-tripped (with their ``data`` payload) so the model can keep
    reasoning across tool turns."""
    client = MagicMock()
    client.messages.create.return_value = _msg_response(content=[
        SimpleNamespace(type="redacted_thinking", data="opaque-blob"),
        SimpleNamespace(type="text", text="answer", citations=[]),
    ])
    provider = _make_provider(client)

    result = provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=16000,
        model="claude-opus-4-7",
        system="you are helpful",
        extended_thinking=True,
    )

    blocks = result["assistant_message"]["content"]
    assert blocks[0] == {"type": "redacted_thinking", "data": "opaque-blob"}


# --- stream_chat ----------------------------------------------------------


def test_stream_chat_passes_thinking_kwarg_to_anthropic_stream():
    client = MagicMock()
    final = _msg_response()
    stream_ctx = MagicMock()
    stream_ctx.__iter__.return_value = iter([])
    stream_ctx.get_final_message.return_value = final
    stream_ctx.__enter__.return_value = stream_ctx
    stream_ctx.__exit__.return_value = False
    client.messages.stream.return_value = stream_ctx

    provider = _make_provider(client)
    list(provider.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16000,
        model="claude-opus-4-7",
        system="you are helpful",
        extended_thinking=True,
    ))

    kwargs = client.messages.stream.call_args.kwargs
    assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 8000}


def test_stream_chat_yields_thinking_event_for_thinking_delta():
    """Anthropic emits ``thinking_delta`` events alongside ``text_delta``
    when extended thinking is on. They must surface as ``{"thinking": ...}``
    provider events, separate from visible text."""
    client = MagicMock()
    final = _msg_response()
    events = [
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="thinking_delta", thinking="step "),
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="thinking_delta", thinking="one"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="answer"),
        ),
    ]
    stream_ctx = MagicMock()
    stream_ctx.__iter__.return_value = iter(events)
    stream_ctx.get_final_message.return_value = final
    stream_ctx.__enter__.return_value = stream_ctx
    stream_ctx.__exit__.return_value = False
    client.messages.stream.return_value = stream_ctx

    provider = _make_provider(client)
    out = list(provider.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16000,
        model="claude-opus-4-7",
        system="x",
        extended_thinking=True,
    ))

    thinking_events = [e for e in out if "thinking" in e and "delta" not in e]
    deltas = [e for e in out if "delta" in e]
    assert [e["thinking"] for e in thinking_events] == ["step ", "one"]
    assert [e["delta"] for e in deltas] == ["answer"]


# --- engine integration --------------------------------------------------


def test_engine_does_not_send_extended_thinking_to_non_anthropic():
    """FakeAIProvider's tools/stream signatures don't accept extended_thinking;
    the engine must gate the kwarg on ``isinstance(provider, AnthropicProvider)``
    so non-Anthropic providers never see it."""
    from tests._fake_ai_provider import FakeAIProvider
    from services.chat.engine import ChatTurn, run_agentic

    fake = FakeAIProvider()
    fake.queue_tools_response(text="hello", tool_calls=[])
    fake.queue_stream_chat_chunks(["hello"])

    turn = ChatTurn(
        provider=fake,
        system_text="system",
        messages=[{"role": "user", "content": "hi"}],
        model="fake-quality",
        extended_thinking=True,
    )

    _drain(run_agentic(turn))

    assert "extended_thinking" not in fake.tools_calls[0]
    assert "extended_thinking" not in fake.stream_chat_calls[0]


def test_engine_emits_thinking_event_for_provider_thinking_text():
    """When the provider returns a ``thinking`` field on the tool-loop result,
    the engine emits a ``thinking`` event before any tool / answer events."""
    from services.chat.engine import ChatTurn, run_agentic

    class StubAnthropic(AnthropicProvider):
        def __init__(self):
            self.client = None
            self.FAST_MODEL = "claude-haiku-4-5-20251001"
            self.QUALITY_MODEL = "claude-opus-4-7"
            self.OPUS_MODEL = "claude-opus-4-7"

        def supports_native_tools(self):
            return True

        def capabilities(self):
            from services.ai_service import ProviderCapabilities
            return ProviderCapabilities(
                provider="anthropic", model=self.QUALITY_MODEL,
                tools=True, vision=True, streaming=True,
            )

        def complete_with_tools(self, **kwargs):
            return {
                "stop_reason": "end_turn",
                "text": "final answer",
                "tool_calls": [],
                "citations": [],
                "thinking": "I considered the wash-sale rule",
                "assistant_message": {"role": "assistant", "content": []},
                "usage": {"input_tokens": 5, "output_tokens": 5, "model": self.QUALITY_MODEL},
            }

        def stream_chat(self, **kwargs):
            yield {"delta": "final answer"}
            yield {"done": True, "usage": {"input_tokens": 1, "output_tokens": 2, "model": self.QUALITY_MODEL}}

    provider = StubAnthropic()
    turn = ChatTurn(
        provider=provider,
        system_text="system",
        messages=[{"role": "user", "content": "harvest losses"}],
        model="claude-opus-4-7",
        extended_thinking=True,
    )

    events = _drain(run_agentic(turn))
    thinking = [e for e in events if e["type"] == "thinking"]
    assert any("wash-sale" in e["text"] for e in thinking)


def test_engine_forwards_extended_thinking_kwarg_to_anthropic():
    """The engine must pass extended_thinking through to AnthropicProvider's
    complete_with_tools and stream_chat when the flag is set on the turn."""
    from services.chat.engine import ChatTurn, run_agentic

    captured: dict[str, list[dict]] = {"tools": [], "stream": []}

    class CapturingAnthropic(AnthropicProvider):
        def __init__(self):
            self.client = None
            self.FAST_MODEL = "claude-haiku-4-5-20251001"
            self.QUALITY_MODEL = "claude-opus-4-7"
            self.OPUS_MODEL = "claude-opus-4-7"

        def supports_native_tools(self):
            return True

        def capabilities(self):
            from services.ai_service import ProviderCapabilities
            return ProviderCapabilities(
                provider="anthropic", model=self.QUALITY_MODEL,
                tools=True, vision=True, streaming=True,
            )

        def complete_with_tools(self, **kwargs):
            captured["tools"].append(kwargs)
            return {
                "stop_reason": "end_turn",
                "text": "ok",
                "tool_calls": [],
                "citations": [],
                "thinking": "",
                "assistant_message": {"role": "assistant", "content": []},
                "usage": {"input_tokens": 1, "output_tokens": 1, "model": self.QUALITY_MODEL},
            }

        def stream_chat(self, **kwargs):
            captured["stream"].append(kwargs)
            yield {"delta": "ok"}
            yield {"done": True, "usage": {"input_tokens": 1, "output_tokens": 1, "model": self.QUALITY_MODEL}}

    provider = CapturingAnthropic()
    turn = ChatTurn(
        provider=provider,
        system_text="system",
        messages=[{"role": "user", "content": "hi"}],
        model="claude-opus-4-7",
        extended_thinking=True,
    )
    _drain(run_agentic(turn))

    assert captured["tools"][0].get("extended_thinking") is True
    assert captured["stream"][0].get("extended_thinking") is True
