"""Unit tests for services.chat.engine.

Drive the engine against ``FakeAIProvider`` (no real LLM, no real MCP tools).
Tool execution inside the engine is monkeypatched so each test scripts both
the LLM responses and the tool results, then asserts the exact event stream
the engine produces.

Run with:
    pytest tests/test_chat_engine.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, AsyncIterator

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.chat import engine as engine_mod
from services.chat.engine import (
    ChatTurn,
    collect,
    complete_one_shot,
    run_agentic,
    run_one_shot,
    run_rag_synthesis,
)
from tests._fake_ai_provider import FakeAIProvider


def _drain(agen: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run an async generator to completion and return all events."""
    async def _go():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_go())


def _ev_types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


# -- run_agentic: native-tools loop ---------------------------------------

def test_agentic_terminates_on_no_tool_turn(monkeypatch):
    """Iteration with tool calls runs them, next iteration with no tool calls
    triggers the streaming final-answer pass."""
    fake = FakeAIProvider()
    fake.queue_tools_response(tool_calls=[
        {"id": "t1", "name": "list_tables", "input": {}},
    ])
    fake.queue_tools_response(tool_calls=[])  # detection: no more tools
    fake.queue_stream_chat_chunks(["The answer ", "is 42."])

    monkeypatch.setattr(
        engine_mod, "execute_tool_calls",
        lambda calls, agent_context=None: [
            {"tool": "list_tables", "result": {"tables": ["t1"]}}
        ],
    )

    turn = ChatTurn(
        provider=fake,
        system_text="You are a helpful assistant.",
        messages=[{"role": "user", "content": "show tables"}],
        model="fake-fast",
        max_iterations=4,
    )
    events = _drain(run_agentic(turn))
    types = _ev_types(events)

    assert types[0] == "tool_start" and events[0]["tool"] == "list_tables"
    assert types[1] == "tool_end"
    deltas = [e["delta"] for e in events if e["type"] == "text_delta"]
    assert deltas == ["The answer ", "is 42."]
    done = [e for e in events if e["type"] == "done"][0]
    assert done["response_text"] == "The answer is 42."
    # Two complete_with_tools calls (the iteration itself only runs once
    # before tool_calls=[] exits the loop) plus one stream_chat call.
    assert len(fake.tools_calls) == 2
    assert len(fake.stream_chat_calls) == 1


def test_agentic_streams_thinking_before_tool_calls(monkeypatch):
    """Narration emitted alongside tool calls becomes a thinking event."""
    fake = FakeAIProvider()
    fake.queue_tools_response(
        text="Let me look that up.",
        tool_calls=[{"id": "t1", "name": "list_tables", "input": {}}],
    )
    fake.queue_tools_response(tool_calls=[])
    fake.queue_stream_chat_chunks(["Done."])

    monkeypatch.setattr(
        engine_mod, "execute_tool_calls",
        lambda calls, agent_context=None: [{"tool": "list_tables", "result": {}}],
    )

    turn = ChatTurn(
        provider=fake, system_text="sys",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
    )
    events = _drain(run_agentic(turn))

    thinking = [e for e in events if e["type"] == "thinking"]
    assert len(thinking) == 1
    assert thinking[0]["text"] == "Let me look that up."


def test_agentic_iteration_cap_forces_streaming_final(monkeypatch):
    """When max_iterations is exhausted with the model still asking for tools,
    a forced no-tools streaming pass produces the final answer."""
    fake = FakeAIProvider()
    # max_iterations=2: queue 2 tool-calling responses, then the forced pass.
    for _ in range(2):
        fake.queue_tools_response(tool_calls=[
            {"id": "tx", "name": "list_tables", "input": {}}
        ])
    fake.queue_stream_chat_chunks(["Forced ", "answer."])

    monkeypatch.setattr(
        engine_mod, "execute_tool_calls",
        lambda calls, agent_context=None: [{"tool": "list_tables", "result": {}}],
    )

    turn = ChatTurn(
        provider=fake, system_text="sys",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
        max_iterations=2,
    )
    events = _drain(run_agentic(turn))

    deltas = [e["delta"] for e in events if e["type"] == "text_delta"]
    assert "".join(deltas) == "Forced answer."
    done = [e for e in events if e["type"] == "done"][0]
    assert done["response_text"] == "Forced answer."

    # Forced pass should have appended "Do not call any more tools" to the
    # system prompt.
    assert len(fake.stream_chat_calls) == 1
    forced_system = fake.stream_chat_calls[0]["system"]
    assert "Do not call any more tools" in forced_system


def test_agentic_forced_pass_failure_emits_fallback_message(monkeypatch):
    """If the forced final-answer streaming pass fails outright, the engine
    emits a deterministic fallback message instead of an empty response."""
    fake = FakeAIProvider()
    for _ in range(2):
        fake.queue_tools_response(tool_calls=[
            {"id": "tx", "name": "list_tables", "input": {}}
        ])
    fake.queue_stream_chat_error(RuntimeError("network blip"))

    monkeypatch.setattr(
        engine_mod, "execute_tool_calls",
        lambda calls, agent_context=None: [{"tool": "list_tables", "result": {}}],
    )

    turn = ChatTurn(
        provider=fake, system_text="sys",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
        max_iterations=2,
    )
    events = _drain(run_agentic(turn))
    done = [e for e in events if e["type"] == "done"][0]
    assert "couldn't settle on a final answer" in done["response_text"]


def test_agentic_falls_back_to_thinking_when_streaming_fails(monkeypatch):
    """If natural termination's streaming pass fails but the non-streaming
    detection call already produced text, the engine surfaces that text."""
    fake = FakeAIProvider()
    fake.queue_tools_response(text="Already-buffered answer.", tool_calls=[])
    fake.queue_stream_chat_error(RuntimeError("kaboom"))

    turn = ChatTurn(
        provider=fake, system_text="sys",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
    )
    events = _drain(run_agentic(turn))
    done = [e for e in events if e["type"] == "done"][0]
    assert done["response_text"] == "Already-buffered answer."
    deltas = [e["delta"] for e in events if e["type"] == "text_delta"]
    assert "".join(deltas) == "Already-buffered answer."


def test_agentic_propagates_provider_error_in_loop():
    """Provider failure during the tool-calling loop terminates with an error
    event — not a fallback message — so the caller can surface it."""
    fake = FakeAIProvider()
    # No tools_responses queued → complete_with_tools raises RuntimeError.

    turn = ChatTurn(
        provider=fake, system_text="sys",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
    )
    events = _drain(run_agentic(turn))
    assert events[-1]["type"] == "error"
    assert "exhausted tools_responses" in events[-1]["message"]


def test_agentic_accumulates_usage_across_iterations_and_stream(monkeypatch):
    """Usage from each tool-calling iteration plus the final streaming pass
    is summed into the done event's usage."""
    fake = FakeAIProvider()
    fake.queue_tools_response(
        tool_calls=[{"id": "t1", "name": "list_tables", "input": {}}],
        usage={"input_tokens": 100, "output_tokens": 10, "model": "fake-fast"},
    )
    fake.queue_tools_response(
        tool_calls=[],
        usage={"input_tokens": 50, "output_tokens": 5, "model": "fake-fast"},
    )
    fake.queue_stream_chat_chunks(["a", "b", "c"])  # contributes 1/3 in fake usage

    monkeypatch.setattr(
        engine_mod, "execute_tool_calls",
        lambda calls, agent_context=None: [{"tool": "list_tables", "result": {}}],
    )

    turn = ChatTurn(
        provider=fake, system_text="sys",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
    )
    events = _drain(run_agentic(turn))
    done = [e for e in events if e["type"] == "done"][0]
    assert done["usage"]["input_tokens"] == 100 + 50 + 1
    assert done["usage"]["output_tokens"] == 10 + 5 + 3


# -- run_agentic: ReAct fallback ------------------------------------------

def test_react_fallback_loops_through_tool_calls(monkeypatch):
    """Provider without native tools triggers the ReAct prose-parsing loop."""
    fake = FakeAIProvider(native_tools=False)
    fake.queue_complete_response(text=(
        '<tool_call>{"tool": "list_tables"}</tool_call>'
    ))
    fake.queue_complete_response(text="Tables are: t1, t2.")

    monkeypatch.setattr(
        engine_mod, "execute_tool_calls",
        lambda calls, agent_context=None: [
            {"tool": "list_tables", "result": {"tables": ["t1", "t2"]}}
        ],
    )

    turn = ChatTurn(
        provider=fake, system_text="sys",
        messages=[{"role": "user", "content": "list tables"}],
        model="fake-fast",
    )
    events = _drain(run_agentic(turn))
    types = _ev_types(events)

    assert "tool_start" in types and "tool_end" in types
    done = [e for e in events if e["type"] == "done"][0]
    assert done["response_text"] == "Tables are: t1, t2."


# -- run_one_shot ----------------------------------------------------------

def test_one_shot_streams_via_provider_stream():
    """OneShotEngine consumes provider.stream and forwards each delta."""
    fake = FakeAIProvider()
    fake.queue_stream_chunks(["Hello ", "world."])

    turn = ChatTurn(
        provider=fake, system_text="prefix",
        messages=[{"role": "user", "content": "say hi"}],
        model="fake-fast",
    )
    events = _drain(run_one_shot(turn))
    deltas = [e["delta"] for e in events if e["type"] == "text_delta"]
    assert deltas == ["Hello ", "world."]
    done = [e for e in events if e["type"] == "done"][0]
    assert done["response_text"] == "Hello world."

    # The system text should be prepended to the prompt.
    assert fake.stream_calls[0]["prompt"].startswith("prefix")


def test_one_shot_propagates_provider_error():
    fake = FakeAIProvider()
    fake.queue_stream_error(ConnectionError("down"))

    turn = ChatTurn(
        provider=fake, system_text="",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
    )
    events = _drain(run_one_shot(turn))
    assert events[-1]["type"] == "error"


# -- run_rag_synthesis -----------------------------------------------------
#
# Simple RAG path used for kind=general / kind=meetings collections — one
# streamed completion over pre-retrieved chunks, no tool loop. Must (a) make
# exactly one provider call, (b) go through stream_chat (not stream) so
# native-citation `documents` flow through, (c) emit the same event shape
# the SSE pipeline already handles.

def test_rag_synthesis_makes_one_stream_chat_call_no_tool_loop():
    fake = FakeAIProvider()
    fake.queue_stream_chat_chunks(["The answer ", "is in the docs."])

    turn = ChatTurn(
        provider=fake, system_text="sys",
        messages=[{"role": "user", "content": "what is a DET?"}],
        model="fake-fast",
    )
    events = _drain(run_rag_synthesis(turn))

    # Exactly one stream_chat call — no detection pass, no agentic loop.
    assert len(fake.stream_chat_calls) == 1, (
        "rag_synthesis must not loop — one streamed call only"
    )
    # Must not have touched complete_with_tools (the agentic detection API).
    assert len(getattr(fake, "tools_calls", [])) == 0

    deltas = [e["delta"] for e in events if e["type"] == "text_delta"]
    assert deltas == ["The answer ", "is in the docs."]
    done = [e for e in events if e["type"] == "done"][0]
    assert done["response_text"] == "The answer is in the docs."


def test_rag_synthesis_propagates_provider_error():
    fake = FakeAIProvider()
    fake.queue_stream_chat_error(ConnectionError("down"))

    turn = ChatTurn(
        provider=fake, system_text="",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
    )
    events = _drain(run_rag_synthesis(turn))
    assert events[-1]["type"] == "error"


def test_rag_synthesis_never_emits_tool_events():
    """The SSE handler at /api/chat/stream branches on event type. The simple
    path must not surface tool_start / tool_end / thinking events the loop
    would otherwise produce, so consumers can treat them as agentic-only."""
    fake = FakeAIProvider()
    fake.queue_stream_chat_chunks(["x"])

    turn = ChatTurn(
        provider=fake, system_text="",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
    )
    events = _drain(run_rag_synthesis(turn))
    types = {e["type"] for e in events}
    assert "tool_start" not in types
    assert "tool_end" not in types
    assert "thinking" not in types


# -- collect ---------------------------------------------------------------

def test_collect_drains_all_event_types(monkeypatch):
    """collect() pairs tool_start args with tool_end results and records
    thinking events as _thinking entries — the legacy response shape."""
    fake = FakeAIProvider()
    fake.queue_tools_response(
        text="Thinking out loud.",
        tool_calls=[{"id": "t1", "name": "list_tables", "input": {"x": 1}}],
    )
    fake.queue_tools_response(tool_calls=[])
    fake.queue_stream_chat_chunks(["Done."])

    monkeypatch.setattr(
        engine_mod, "execute_tool_calls",
        lambda calls, agent_context=None: [
            {"tool": "list_tables", "result": {"tables": ["a"]}}
        ],
    )

    turn = ChatTurn(
        provider=fake, system_text="sys",
        messages=[{"role": "user", "content": "q"}],
        model="fake-fast",
    )

    result = asyncio.run(collect(run_agentic(turn)))

    assert result["error"] is None
    assert result["text"] == "Done."
    tool_entries = [r for r in result["executed_results"] if r["tool"] == "list_tables"]
    assert len(tool_entries) == 1
    assert tool_entries[0]["args"] == {"x": 1}
    # collect() stores the full execute_tool_calls wrapper under "result"
    # (which itself contains tool/result keys) — that's the legacy shape
    # /api/chat callers persist verbatim.
    assert tool_entries[0]["result"]["result"] == {"tables": ["a"]}
    thinking_entries = [r for r in result["executed_results"] if r["tool"] == "_thinking"]
    assert len(thinking_entries) == 1
    assert thinking_entries[0]["result"]["text"] == "Thinking out loud."


# -- complete_one_shot helper ---------------------------------------------

def test_complete_one_shot_returns_buffered_dict():
    fake = FakeAIProvider()
    fake.queue_stream_chunks(["hi"])

    result = asyncio.run(complete_one_shot(
        provider=fake, prompt="prompt", model="fake-fast",
    ))
    assert result["text"] == "hi"
    assert result["error"] is None


def test_complete_one_shot_raises_on_error():
    fake = FakeAIProvider()
    fake.queue_stream_error(RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        asyncio.run(complete_one_shot(
            provider=fake, prompt="p", model="fake-fast",
        ))


# -- AIProvider default streaming fallback --------------------------------

def test_default_stream_chat_falls_back_to_complete_with_tools():
    """The AIProvider base class supplies a non-streaming default that
    emits one full-text delta + done. Providers that haven't implemented
    real streaming yet still work — they just look like instant responses."""
    from services.ai_service import AIProvider

    fake = FakeAIProvider()
    fake.queue_tools_response(text="single delta", tool_calls=[])
    events = list(AIProvider.stream_chat(
        fake, messages=[], max_tokens=10, model="m", system=None,
    ))
    deltas = [e["delta"] for e in events if "delta" in e]
    assert deltas == ["single delta"]
    assert events[-1].get("done") is True


def test_default_stream_falls_back_to_complete():
    """Symmetric default for the prompt-based stream."""
    from services.ai_service import AIProvider

    fake = FakeAIProvider()
    fake.queue_complete_response(text="full text")
    events = list(AIProvider.stream(fake, prompt="p", max_tokens=10, model="m"))
    deltas = [e["delta"] for e in events if "delta" in e]
    assert deltas == ["full text"]
    assert events[-1].get("done") is True
