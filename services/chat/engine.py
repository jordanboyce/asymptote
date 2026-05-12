"""Chat engines — the LLM execution loop that endpoints share.

Two engines, one event vocabulary:

  * AgenticEngine (run_agentic) — drives a tool-use loop until the model
    produces a turn with no tool calls or max_iterations is hit. Used by
    /api/chat/stream and any future slash command that needs tool access.

  * OneShotEngine (run_one_shot) — drives a single completion. Used by
    /api/notes, /api/followup, /api/ask and any future drafting endpoint
    that doesn't need tool access.

Both are async generators emitting events:

    thinking    — prose narration the model emitted alongside tool calls
                  fields: iteration, text
    tool_start  — a tool call is about to execute
                  fields: tool, args
    tool_end    — tool call finished
                  fields: tool, result
    text_delta  — one chunk of the final answer
                  fields: delta
    citation    — Anthropic native-citation block (resolved against the
                  retrieved chunks the provider was given)
                  fields: document_id, chunk_id, page_number, filename,
                          cited_text, start_char, end_char
    done        — completion marker
                  fields: usage, response_text
    error       — something failed
                  fields: message

This vocabulary matches what /api/chat/stream already emits over SSE so the
frontend doesn't change. Non-streaming callers use ``collect()`` to drain
the events into a result dict.

Streaming: the final answer is emitted via real provider streaming —
Anthropic ``messages.stream``, OpenAI ``stream=True``, Ollama
``/api/chat stream=true``. Providers that don't override the streaming
methods on ``AIProvider`` fall back to a single delta containing the
full non-streamed text, so every provider keeps working.

The agentic loop still runs ``complete_with_tools`` for tool-call turns;
when an iteration returns no tool calls (or the iteration cap is hit and
a forced pass runs), that single final-answer turn is re-issued via
``provider.stream_chat``. One extra LLM call per chat turn — accepted
trade until whole-loop streaming (Phase 3) lands.

ReAct fallback (providers without native tool calling, e.g. Ollama) still
fake-streams its final answer via ``_emit_text_as_deltas``; promoting
ReAct to real streaming is its own follow-up.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from services.agent_tools import anthropic_tools, openai_tools
from services.ai_service import AIProvider, AnthropicProvider
from services.chat.think_tags import StreamingThinkStripper, strip_think_tags
from services.structured_chat import (
    build_tool_use_instructions,
    execute_tool_calls,
    format_results_for_prompt,
    parse_tool_calls,
    strip_tool_calls,
)

logger = logging.getLogger(__name__)


@dataclass
class ChatTurn:
    """Frozen, prepared input for one engine run.

    Endpoints assemble this from a request + context; the engine consumes it.
    """

    provider: AIProvider
    system_text: str
    messages: list[dict[str, Any]]
    agent_context: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    max_tokens: int = 2048
    max_iterations: int = 8
    # When true, narration the model emits alongside tool calls is yielded as
    # `thinking` events so SSE callers can render it. collect() records them
    # as `_thinking` entries in executed_results for the legacy response shape.
    emit_thinking: bool = True
    # Anthropic native-citation document blocks (one per retrieved chunk),
    # plus a parallel metadata list indexed by ``document_index`` so citation
    # events can be enriched with document_id / chunk_id / page / filename
    # before they leave the engine. Both empty for non-Anthropic providers.
    documents: list[dict[str, Any]] = field(default_factory=list)
    document_metadata: list[dict[str, Any]] = field(default_factory=list)
    # When true, ask Anthropic thinking-capable models to allocate a reasoning
    # budget before the visible answer. Per-turn opt-in — chat default is
    # False because the per-turn cost balloons on trivial questions; specific
    # slash commands (TLH wash-sale checks, rebalance trade ordering, future
    # Monte Carlo) opt in by setting this True via ChatRequest. Silently
    # ignored on non-Anthropic providers and on non-thinking-capable models.
    extended_thinking: bool = False
    # Optional allowlist of tool names the agent may use. When None (default),
    # the engine advertises the full canonical tool list. When set, the engine
    # filters the spec list down to exactly these names — used by
    # ``services.collection_context`` to keep financial tools off the menu for
    # docs-only collections (and vice versa). Set is more efficient than list
    # for repeated membership checks.
    allowed_tool_names: frozenset[str] | None = None


async def run_agentic(turn: ChatTurn) -> AsyncIterator[dict[str, Any]]:
    """Drive the agentic tool-use loop, yielding events as they happen."""

    # Refuse upfront when the (provider, model) pair plainly can't run the
    # tool-use loop. Catches three failure modes that previously surfaced as
    # opaque mid-stream provider errors:
    #   1. model="" (empty string) → 404 from OpenAI-compatible endpoints
    #   2. capabilities().tools is False → known-incapable model picked
    #   3. provider.supports_native_tools() False → routes to ReAct fallback
    if not turn.model:
        provider_name = turn.provider.__class__.__name__
        yield {
            "type": "error",
            "message": (
                f"No model selected for {provider_name}. Open Settings, "
                "validate the API key, and pick a model before starting chat."
            ),
        }
        return

    capabilities = turn.provider.capabilities()
    if capabilities.tools is False and turn.provider.supports_native_tools():
        # Provider claims native tool calling, but capabilities() declares the
        # current model can't actually do it. Refuse with a clear message
        # instead of letting the API call return tool-shaped errors.
        yield {
            "type": "error",
            "message": (
                f"Model '{capabilities.model}' on {capabilities.provider} doesn't "
                "support tool calling. Pick a tool-capable model in Settings — "
                "Anthropic Claude, OpenAI gpt-4o family, or Ollama Cloud gpt-oss."
            ),
        }
        return

    if not turn.provider.supports_native_tools():
        async for ev in _run_react_fallback(turn):
            yield ev
        return

    is_anthropic = isinstance(turn.provider, AnthropicProvider)
    tools_spec = anthropic_tools() if is_anthropic else openai_tools()
    if turn.allowed_tool_names is not None:
        from services.collection_context import filter_tool_specs
        tools_spec = filter_tool_specs(tools_spec, allowed=turn.allowed_tool_names)
    # Native-citation documents are Anthropic-only; passing the kwarg to other
    # providers would raise. The ``or None`` keeps the wire payload tidy when
    # context.py decided not to populate documents for this turn.
    provider_documents = turn.documents if is_anthropic else None
    # Same gate for extended thinking — only Anthropic implements it; other
    # providers' kwargs lists don't include the parameter.
    enable_thinking = bool(turn.extended_thinking) and is_anthropic

    messages = list(turn.messages)
    totals = _UsageTotals(model=turn.model)
    response_text = ""

    for iteration in range(turn.max_iterations):
        try:
            tool_kwargs: dict[str, Any] = {
                "messages": messages,
                "tools": tools_spec,
                "max_tokens": turn.max_tokens,
                "model": turn.model,
                "system": turn.system_text,
            }
            if provider_documents:
                tool_kwargs["documents"] = provider_documents
            if enable_thinking:
                tool_kwargs["extended_thinking"] = True
            result = turn.provider.complete_with_tools(**tool_kwargs)
        except Exception as e:
            logger.exception("[engine] provider call failed at iter=%d", iteration)
            yield {"type": "error", "message": f"Provider call failed: {e}"}
            return

        totals.add(result.get("usage"))

        tool_calls = result.get("tool_calls") or []
        # Strip `<think>...</think>` reasoning blocks so they don't surface
        # as a thinking-event bullet or as a streaming-failure fallback.
        thinking = strip_think_tags(result.get("text") or "")

        # Extended-thinking text from Anthropic comes back as a separate field
        # (real reasoning blocks, not embedded `<think>` markers). Emit them as
        # `thinking` events ahead of any prose narration so the UI shows the
        # reasoning trace in the order the model produced it.
        extended_thinking_text = result.get("thinking") or ""
        if extended_thinking_text and turn.emit_thinking:
            yield {
                "type": "thinking",
                "iteration": iteration,
                "text": extended_thinking_text,
            }

        for citation in result.get("citations") or []:
            yield _enrich_citation(citation, turn.document_metadata)

        logger.info(
            "[engine] iter=%d stop=%s tool_calls=%d",
            iteration, result.get("stop_reason"), len(tool_calls),
        )

        if not tool_calls:
            # Final-answer turn — re-issue with streaming so the user sees
            # tokens as the model generates them. One extra LLM call; the
            # `thinking` text from the non-streaming detection call is the
            # safety net if streaming fails or returns nothing.
            stream_kwargs: dict[str, Any] = {
                "messages": messages,
                "max_tokens": turn.max_tokens,
                "model": turn.model,
                "system": turn.system_text,
            }
            if provider_documents:
                stream_kwargs["documents"] = provider_documents
            if enable_thinking:
                stream_kwargs["extended_thinking"] = True
            streamed_text, streamed_usage = "", None
            try:
                async for ev in _drive_provider_stream(
                    turn.provider.stream_chat(**stream_kwargs),
                    document_metadata=turn.document_metadata,
                ):
                    if ev["type"] == "_done":
                        streamed_text = ev["text"]
                        streamed_usage = ev["usage"]
                    else:
                        yield ev
            except Exception as e:
                logger.warning("[engine] streaming final answer failed: %s", e)

            totals.add(streamed_usage)

            if streamed_text:
                response_text = streamed_text
            elif thinking.strip():
                response_text = thinking.strip()
                async for ev in _emit_text_as_deltas(response_text):
                    yield ev
            break

        if thinking and turn.emit_thinking:
            yield {"type": "thinking", "iteration": iteration, "text": thinking}

        for tc in tool_calls:
            yield {
                "type": "tool_start",
                "tool": tc["name"],
                "args": tc.get("input") or {},
            }

        messages.append(result["assistant_message"])

        adapted_calls = [
            {"tool": tc["name"], **(tc.get("input") or {})}
            for tc in tool_calls
        ]
        iter_results = execute_tool_calls(adapted_calls, agent_context=turn.agent_context)

        for tc, res in zip(tool_calls, iter_results):
            yield {"type": "tool_end", "tool": tc["name"], "result": res}

        if is_anthropic:
            tool_result_content = []
            for tc, res in zip(tool_calls, iter_results):
                payload = res.get("error") or res.get("result") or {}
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": json.dumps(payload, default=str)[:60000],
                    **({"is_error": True} if res.get("error") else {}),
                })
            messages.append({"role": "user", "content": tool_result_content})
        else:
            for tc, res in zip(tool_calls, iter_results):
                payload = res.get("error") or res.get("result") or {}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(payload, default=str)[:60000],
                })

    if not response_text:
        # Iteration cap hit — force a final answer with no tools, streamed.
        forced_kwargs: dict[str, Any] = {
            "messages": messages,
            "max_tokens": turn.max_tokens,
            "model": turn.model,
            "system": turn.system_text + "\n\nDo not call any more tools. Summarize the final answer.",
        }
        if provider_documents:
            forced_kwargs["documents"] = provider_documents
        if enable_thinking:
            forced_kwargs["extended_thinking"] = True
        streamed_text, streamed_usage = "", None
        try:
            async for ev in _drive_provider_stream(
                turn.provider.stream_chat(**forced_kwargs),
                document_metadata=turn.document_metadata,
            ):
                if ev["type"] == "_done":
                    streamed_text = ev["text"]
                    streamed_usage = ev["usage"]
                else:
                    yield ev
        except Exception as e:
            logger.warning("[engine] forced streaming final-answer pass failed: %s", e)

        totals.add(streamed_usage)
        response_text = streamed_text
        if not response_text:
            response_text = (
                "I ran several tool calls but couldn't settle on a final answer "
                "— please rephrase or narrow the question."
            )
            async for ev in _emit_text_as_deltas(response_text):
                yield ev

    yield {
        "type": "done",
        "usage": totals.to_dict(),
        "response_text": response_text,
    }


async def run_rag_synthesis(turn: ChatTurn) -> AsyncIterator[dict[str, Any]]:
    """Single streamed completion over pre-retrieved chunks — no tool loop.

    Use this for collection kinds (``general`` / ``meetings``) where the chunks
    already in ``turn.documents`` / ``turn.system_text`` are sufficient and the
    only tools we could expose are ``search_documents`` (already pre-run by
    ``ChatContext._run_search``) and meta-tools that don't help simple Q&A. The
    agentic loop in :func:`run_agentic` would just re-call ``search_documents``
    a few times before producing the same answer — ~5× the latency, no quality
    win. Restoring the classic RAG path here gets us back to one model call.

    Emits the same event vocabulary as :func:`run_agentic` (``text_delta`` /
    ``done`` / ``error`` / Anthropic citation events) so the SSE pipeline at
    ``/api/chat/stream`` doesn't need to special-case which engine ran.

    Differs from :func:`run_one_shot`: that path is for drafting endpoints
    (/notes /followup /ask) which call ``provider.stream(prompt=...)`` with a
    flat prompt string. This path keeps the chat-shaped messages and passes
    ``documents`` through so Anthropic native citations work.
    """
    is_anthropic = isinstance(turn.provider, AnthropicProvider)
    enable_thinking = bool(turn.extended_thinking) and is_anthropic

    stream_kwargs: dict[str, Any] = {
        "messages": list(turn.messages),
        "max_tokens": turn.max_tokens,
        "model": turn.model,
        "system": turn.system_text,
    }
    if is_anthropic and turn.documents:
        stream_kwargs["documents"] = turn.documents
    if enable_thinking:
        stream_kwargs["extended_thinking"] = True

    streamed_text, streamed_usage = "", None
    try:
        async for ev in _drive_provider_stream(
            turn.provider.stream_chat(**stream_kwargs),
            document_metadata=turn.document_metadata,
        ):
            if ev["type"] == "_done":
                streamed_text = ev["text"]
                streamed_usage = ev["usage"]
            else:
                yield ev
    except Exception as e:
        logger.exception("[engine] rag-synthesis streaming failed")
        yield {"type": "error", "message": f"Provider call failed: {e}"}
        return

    response_text = streamed_text.strip()
    usage = streamed_usage or {}

    yield {
        "type": "done",
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "model": usage.get("model", turn.model),
        },
        "response_text": response_text,
    }


async def run_one_shot(turn: ChatTurn) -> AsyncIterator[dict[str, Any]]:
    """Drive a single completion, streaming the result as text_delta events.

    Used by document-drafting endpoints (/notes, /followup, /ask). The
    convention: ``turn.messages`` carries a single user message whose content
    is the prompt body; ``turn.system_text`` is prepended.
    """

    is_anthropic = isinstance(turn.provider, AnthropicProvider)
    enable_thinking = bool(turn.extended_thinking) and is_anthropic

    streamed_text, streamed_usage = "", None
    try:
        if enable_thinking:
            # Extended thinking only flows through the messages-shaped
            # ``stream_chat`` API; route the prompt through that path with the
            # system text sent as `system` so the thinking budget applies.
            latest = turn.messages[-1].get("content", "") if turn.messages else ""
            stream_iter = turn.provider.stream_chat(
                messages=[{"role": "user", "content": latest}],
                max_tokens=turn.max_tokens,
                model=turn.model or "",
                system=turn.system_text or None,
                extended_thinking=True,
            )
        else:
            latest = turn.messages[-1].get("content", "") if turn.messages else ""
            prompt = (turn.system_text + "\n\n" + latest).strip() if turn.system_text else latest
            stream_iter = turn.provider.stream(
                prompt=prompt,
                max_tokens=turn.max_tokens,
                model=turn.model or "",
            )
        async for ev in _drive_provider_stream(stream_iter):
            if ev["type"] == "_done":
                streamed_text = ev["text"]
                streamed_usage = ev["usage"]
            else:
                yield ev
    except Exception as e:
        logger.exception("[engine] one-shot streaming failed")
        yield {"type": "error", "message": f"Provider call failed: {e}"}
        return

    response_text = streamed_text.strip()
    usage = streamed_usage or {}

    yield {
        "type": "done",
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "model": usage.get("model", turn.model),
        },
        "response_text": response_text,
    }


async def _run_react_fallback(turn: ChatTurn) -> AsyncIterator[dict[str, Any]]:
    """ReAct-over-prose loop for providers without native tool-calling (Ollama)."""

    history_parts: list[str] = []
    user_text = ""
    for m in turn.messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "user":
            user_text = content
            history_parts.append(f"User: {content}")
        elif role == "assistant" and isinstance(content, str):
            history_parts.append(f"Assistant: {content}")
    history_text = (
        "\n\n".join(history_parts[:-1])
        if len(history_parts) > 1 else "(Start of conversation)"
    )
    tools_block = build_tool_use_instructions()

    def compose(extra: str = "") -> str:
        parts = [
            turn.system_text,
            "",
            f"TOOL USE PROTOCOL:\n{tools_block}",
            "",
            f"CONVERSATION HISTORY:\n{history_text}",
            "",
            f"User: {user_text}",
        ]
        if extra:
            parts.append(extra)
        parts.append("Assistant:")
        return "\n\n".join(parts)

    suffix = ""
    total_input_tokens = 0
    total_output_tokens = 0
    model_used = turn.model
    response_text = ""

    for iteration in range(min(turn.max_iterations, 5)):
        try:
            result = turn.provider.complete(
                prompt=compose(suffix),
                max_tokens=turn.max_tokens,
                model=turn.model or "",
            )
        except Exception as e:
            yield {"type": "error", "message": f"ReAct provider call failed: {e}"}
            return

        raw_text = result["text"]
        usage_iter = result.get("usage", {}) or {}
        total_input_tokens += usage_iter.get("input_tokens", 0)
        total_output_tokens += usage_iter.get("output_tokens", 0)
        model_used = usage_iter.get("model", model_used)

        calls = parse_tool_calls(raw_text)
        thinking = strip_tool_calls(raw_text).strip()

        if not calls:
            response_text = raw_text.strip()
            break

        if thinking and turn.emit_thinking:
            yield {"type": "thinking", "iteration": iteration, "text": thinking}
        for tc in calls:
            yield {
                "type": "tool_start",
                "tool": tc.get("tool", "unknown"),
                "args": {k: v for k, v in tc.items() if k != "tool"},
            }

        iter_results = execute_tool_calls(calls, agent_context=turn.agent_context)
        for tc, res in zip(calls, iter_results):
            yield {"type": "tool_end", "tool": tc.get("tool", "unknown"), "result": res}

        suffix = (
            (suffix + "\n\n" if suffix else "")
            + f"Assistant (previous turn):\n{raw_text.strip()}\n\n"
            + format_results_for_prompt(iter_results)
            + "\n\nYou may call more tools, or produce the final answer. "
            + "When done, write the answer with no <tool_call> blocks."
        )

    response_text = strip_tool_calls(response_text).strip()

    async for ev in _emit_text_as_deltas(response_text):
        yield ev

    yield {
        "type": "done",
        "usage": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "model": model_used,
        },
        "response_text": response_text,
    }


async def _drive_provider_stream(
    stream_iter: Any,
    document_metadata: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Forward provider stream events as engine events.

    Bridges a sync provider stream (``provider.stream`` /
    ``provider.stream_chat``, which yield ``{"delta": str}`` /
    ``{"citation": {...}}`` / ``{"thinking": str}`` /
    ``{"done": True, "usage": ...}`` dicts) into the engine's async event
    vocabulary. Concludes with a private ``_done`` event carrying the
    accumulated text and usage so the caller can record state without
    re-iterating; ``_done`` is internal — callers must not re-yield it to
    SSE consumers.

    ``document_metadata`` is the parallel mapping used to enrich
    Anthropic-native citation deltas with our document_id/chunk_id.
    """
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    usage: dict[str, Any] | None = None
    stripper = StreamingThinkStripper()
    metadata = document_metadata or []
    for sev in stream_iter:
        if not isinstance(sev, dict):
            continue
        delta = sev.get("delta")
        citation = sev.get("citation")
        thinking = sev.get("thinking")
        if delta:
            visible = stripper.feed(delta)
            if visible:
                text_parts.append(visible)
                yield {"type": "text_delta", "delta": visible}
                await asyncio.sleep(0)
        elif citation is not None:
            yield _enrich_citation(citation, metadata)
        elif thinking:
            # Buffer extended-thinking fragments and emit them as a single
            # `thinking` event when the stream ends — partial mid-thought
            # fragments are not useful to the UI and Anthropic emits these
            # in small chunks.
            thinking_parts.append(thinking)
        elif sev.get("done"):
            usage = sev.get("usage")
    tail = stripper.flush()
    if tail:
        text_parts.append(tail)
        yield {"type": "text_delta", "delta": tail}
    if thinking_parts:
        yield {
            "type": "thinking",
            "iteration": -1,  # streaming-pass thinking is post-loop
            "text": "".join(thinking_parts).strip(),
        }
    yield {"type": "_done", "text": "".join(text_parts), "usage": usage}


def _enrich_citation(
    citation: dict[str, Any],
    document_metadata: list[dict[str, Any]],
) -> dict[str, Any]:
    """Translate a provider citation block to an engine ``citation`` event.

    The provider gives us a 0-based ``document_index`` into the documents
    array we sent in. ``document_metadata`` is the parallel array — same
    indices — that lets us map back to Finn's document_id / chunk_id /
    page_number / filename so the frontend can deep-link to a chunk.
    """
    idx = citation.get("document_index")
    meta: dict[str, Any] = {}
    if isinstance(idx, int) and 0 <= idx < len(document_metadata):
        meta = document_metadata[idx] or {}
    return {
        "type": "citation",
        "document_id": meta.get("document_id"),
        "chunk_id": meta.get("chunk_id"),
        "page_number": meta.get("page_number"),
        "filename": meta.get("filename"),
        "cited_text": citation.get("cited_text"),
        "start_char": citation.get("start_char_index"),
        "end_char": citation.get("end_char_index"),
    }


@dataclass
class _UsageTotals:
    """Running totals for an agentic run's usage accounting."""

    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        self.input_tokens += usage.get("input_tokens", 0) or 0
        self.output_tokens += usage.get("output_tokens", 0) or 0
        self.model = usage.get("model", self.model)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model,
        }


async def _emit_text_as_deltas(
    text: str, *, word_delay: float = 0.008
) -> AsyncIterator[dict[str, Any]]:
    """Emit text as text_delta events one word at a time.

    Fallback for paths that already have buffered text (ReAct fallback,
    error-message synthesis). Real per-token streaming is handled by
    ``_drive_provider_stream``.
    """
    if not text:
        return
    words = text.split(" ")
    for i, word in enumerate(words):
        chunk = word + (" " if i < len(words) - 1 else "")
        yield {"type": "text_delta", "delta": chunk}
        if word_delay > 0:
            await asyncio.sleep(word_delay)


async def complete_one_shot(
    *,
    provider: AIProvider,
    prompt: str,
    model: str | None = None,
    max_tokens: int = 1500,
) -> dict[str, Any]:
    """Run a one-shot completion and return the buffered result.

    Convenience for non-streaming callers (/notes, /followup, /ask) — same
    code path as run_one_shot, just drained into a dict. When real provider
    streaming lands, every caller of this gets it for free.

    Raises ``RuntimeError`` on provider failure so callers can map it to an
    HTTPException without inspecting the result dict.
    """
    turn = ChatTurn(
        provider=provider,
        system_text="",
        messages=[{"role": "user", "content": prompt}],
        model=model,
        max_tokens=max_tokens,
    )
    result = await collect(run_one_shot(turn))
    if result["error"]:
        raise RuntimeError(result["error"])
    return result


async def collect(events: AsyncIterator[dict[str, Any]]) -> dict[str, Any]:
    """Drain an event stream into a final result dict.

    Used by JSON endpoints — same engine, same events, just buffered.
    """
    text_parts: list[str] = []
    executed_results: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    pending_args: dict[str, dict[str, Any]] = {}
    usage = {"input_tokens": 0, "output_tokens": 0, "model": None}
    error: str | None = None
    response_text = ""

    async for ev in events:
        t = ev.get("type")
        if t == "text_delta":
            text_parts.append(ev.get("delta", ""))
        elif t == "citation":
            citations.append({k: v for k, v in ev.items() if k != "type"})
        elif t == "thinking":
            executed_results.append({
                "tool": "_thinking",
                "args": {"iteration": ev.get("iteration", 0)},
                "result": {"text": ev.get("text", "")},
            })
        elif t == "tool_start":
            pending_args[ev["tool"]] = ev.get("args") or {}
        elif t == "tool_end":
            tool_name = ev.get("tool", "unknown")
            executed_results.append({
                "tool": tool_name,
                "args": pending_args.pop(tool_name, {}),
                "result": ev.get("result", {}),
            })
        elif t == "done":
            usage = ev.get("usage", usage)
            response_text = ev.get("response_text", "".join(text_parts))
        elif t == "error":
            error = ev.get("message", "Unknown error")

    return {
        "text": response_text or "".join(text_parts),
        "executed_results": executed_results,
        "citations": citations,
        "usage": usage,
        "error": error,
    }
