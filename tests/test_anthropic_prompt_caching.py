"""Prompt-caching coverage for AnthropicProvider.

Verifies that the system prompt is wrapped with ``cache_control: ephemeral``
on every messages.create / stream call, that cache token fields flow through
to the returned ``usage`` dict, and that the diagnostics buffer accumulates
cumulative cache stats. The Anthropic SDK is mocked — no network calls.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.ai_service import AnthropicProvider
from services.diagnostics import get_buffer


def _make_provider(client_mock):
    p = AnthropicProvider.__new__(AnthropicProvider)
    p.client = client_mock
    p.FAST_MODEL = "claude-haiku-4-5-20251001"
    p.QUALITY_MODEL = "claude-sonnet-4-6"
    p.OPUS_MODEL = "claude-opus-4-7"
    return p


def _make_message_response(*, cache_creation=0, cache_read=0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=42,
            output_tokens=7,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        ),
    )


@pytest.fixture(autouse=True)
def _clear_diagnostics():
    get_buffer().clear()
    yield
    get_buffer().clear()


def test_complete_with_tools_wraps_system_with_cache_control():
    client = MagicMock()
    client.messages.create.return_value = _make_message_response(cache_creation=512)
    provider = _make_provider(client)

    provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=128,
        model="claude-sonnet-4-6",
        system="You are an analytical assistant for a financial advisor.",
    )

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"] == [{
        "type": "text",
        "text": "You are an analytical assistant for a financial advisor.",
        "cache_control": {"type": "ephemeral"},
    }]


def test_complete_with_tools_omits_system_when_none():
    client = MagicMock()
    client.messages.create.return_value = _make_message_response()
    provider = _make_provider(client)

    provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=128,
        model="claude-sonnet-4-6",
        system=None,
    )

    assert "system" not in client.messages.create.call_args.kwargs


def test_complete_with_tools_surfaces_cache_token_fields():
    client = MagicMock()
    client.messages.create.return_value = _make_message_response(
        cache_creation=1024, cache_read=0,
    )
    provider = _make_provider(client)

    result = provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=128,
        model="claude-sonnet-4-6",
        system="static prefix",
    )

    assert result["usage"]["cache_creation_input_tokens"] == 1024
    assert result["usage"]["cache_read_input_tokens"] == 0
    assert result["usage"]["input_tokens"] == 42


def test_two_calls_simulate_creation_then_read_cache_stats():
    """First call writes the cache; second call reads it. The diagnostics
    buffer accumulates totals so the UI can compute hit rate over time."""
    client = MagicMock()
    client.messages.create.side_effect = [
        _make_message_response(cache_creation=2048, cache_read=0),
        _make_message_response(cache_creation=0, cache_read=2048),
    ]
    provider = _make_provider(client)

    for _ in range(2):
        provider.complete_with_tools(
            messages=[{"role": "user", "content": "same question"}],
            tools=[],
            max_tokens=128,
            model="claude-sonnet-4-6",
            system="static prefix",
        )

    stats = get_buffer().cache_stats()
    assert stats["cache_creation_input_tokens"] == 2048
    assert stats["cache_read_input_tokens"] == 2048
    assert stats["requests"] == 2
    assert stats["hit_rate"] == pytest.approx(0.5)


def test_passthrough_when_system_is_already_a_list():
    """Caller-side cache_control placement is honoured — don't double-wrap."""
    client = MagicMock()
    client.messages.create.return_value = _make_message_response()
    provider = _make_provider(client)

    pre_shaped = [
        {"type": "text", "text": "stable"},
        {"type": "text", "text": "variable", "cache_control": {"type": "ephemeral"}},
    ]
    provider.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=128,
        model="claude-sonnet-4-6",
        system=pre_shaped,
    )

    assert client.messages.create.call_args.kwargs["system"] is pre_shaped


def test_stream_chat_wraps_system_and_records_cache_usage():
    client = MagicMock()

    final = _make_message_response(cache_creation=0, cache_read=4096)
    # The SDK's stream context is iterable — yields content_block_delta /
    # message_delta / message_stop events. AnthropicProvider.stream_chat
    # iterates these directly so it can surface citations_delta alongside
    # text_delta. Mock the same event shape here.
    events = [
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="he"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="llo"),
        ),
    ]
    stream_ctx = MagicMock()
    stream_ctx.__iter__.return_value = iter(events)
    stream_ctx.get_final_message.return_value = final
    stream_ctx.__enter__.return_value = stream_ctx
    stream_ctx.__exit__.return_value = False
    client.messages.stream.return_value = stream_ctx

    provider = _make_provider(client)

    deltas: list[str] = []
    done_event: dict | None = None
    for ev in provider.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=128,
        model="claude-sonnet-4-6",
        system="static prefix",
    ):
        if "delta" in ev:
            deltas.append(ev["delta"])
        elif ev.get("done"):
            done_event = ev

    sys_param = client.messages.stream.call_args.kwargs["system"]
    assert sys_param[0]["cache_control"] == {"type": "ephemeral"}

    assert deltas == ["he", "llo"]
    assert done_event is not None
    assert done_event["usage"]["cache_read_input_tokens"] == 4096

    stats = get_buffer().cache_stats()
    assert stats["cache_read_input_tokens"] == 4096
    assert stats["requests"] == 1
