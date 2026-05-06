"""Chat-specific context builder.

Turns a ChatRequest + provider into a prepared ChatTurn (system text,
messages, agent context) plus the filtered search results the endpoint
needs to build its source list.

Phases (in order):

  1. Resolve scope to a list of collection ids ("current", "all",
     "group:<id>").
  2. Reformulate the latest user message using prior history (skipped on
     single-turn queries to avoid an unnecessary LLM call).
  3. Search the resolved collections, paired with their origin collection
     id so URLs stay correct in cross-collection scopes.
  4. Optionally rerank the merged results.
  5. Collect Holdings tables for the resolved collections — small ones get
     inlined as JSONL, large ones are exposed as tool-callable tables.
  6. Filter out chunks whose files were inlined (the JSONL is authoritative).
  7. Assemble the system prompt: advisor framing + Collection overview +
     Expertise packs (single-collection scope only) + inline JSONL block +
     LARGE TABLES block + RETRIEVED CONTEXT.

/notes, /followup, /ask skip this builder — they assemble their own
prompts from briefs, transcripts, etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from models.schemas import ChatRequest
from services.ai_service import AIProvider, AIService
from services.chat.engine import ChatTurn
from services.collection_overview import build_collection_overview
from services.collection_service import collection_service
from services.expertise_store import ExpertiseStore
from services.indexer_manager import indexer_manager
from services.structured_chat import (
    build_structured_context,
    collect_structured_tables,
    describe_tables_for_prompt,
)

logger = logging.getLogger(__name__)


@dataclass
class PreparedChat:
    """Engine input + the bits the endpoint still needs to shape the response."""

    turn: ChatTurn
    # Search results that survived inlining-filter, paired with their origin
    # collection id. The endpoint uses these to build pdf_url / page_url
    # entries — URLs aren't the engine's concern.
    filtered_results: list[tuple[Any, str]]
    # Reranking call's usage, when reranking ran. Used by /api/chat's legacy
    # AIUsage payload; SSE callers can ignore it.
    rerank_usage: dict[str, Any] | None
    # Whether structured tables are inlined as JSONL (helps endpoint decide
    # if the response should advertise the "structured_tools" feature flag).
    has_executed_tools: bool = False


def build_chat_turn(
    *,
    provider: AIProvider,
    chat_request: ChatRequest,
    collection_id: str,
    expertise_store: ExpertiseStore,
    max_iterations: int = 8,
    max_tokens: int = 4096,
) -> PreparedChat:
    """Run the chat-context phases and return everything the engine needs."""

    user_messages = [m for m in chat_request.messages if m.role == "user"]
    if not user_messages:
        raise ValueError("No user messages in conversation")
    latest_query = user_messages[-1].content

    ai_service = AIService(provider=provider)

    search_query = _reformulate_if_needed(ai_service, chat_request, latest_query)
    search_col_ids = _resolve_scope(chat_request.scope, collection_id)
    context_results, result_collection_ids = _run_search(
        search_col_ids, collection_id, search_query, chat_request,
    )

    rerank_usage = None
    if chat_request.rerank and context_results:
        context_results, result_collection_ids, rerank_usage = _rerank(
            ai_service, latest_query, context_results,
            result_collection_ids, chat_request.top_k,
        )

    overview_ids = search_col_ids if search_col_ids is not None else [collection_id]
    structured_tables, structured_stores = collect_structured_tables(overview_ids)
    if structured_tables:
        structured_ctx = build_structured_context(structured_tables, structured_stores)
    else:
        structured_ctx = {
            "inline_block": "",
            "tool_tables": [],
            "inlined_filenames": set(),
            "inlined_document_ids": set(),
        }
    inlined_filenames = structured_ctx["inlined_filenames"]
    tool_tables = structured_ctx["tool_tables"]
    inline_block = structured_ctx["inline_block"]

    filtered_results = [
        (r, cid) for (r, cid) in zip(context_results, result_collection_ids)
        if r.filename not in inlined_filenames
    ]

    context_text = _format_retrieved_context(filtered_results)

    try:
        collection_overview = build_collection_overview(overview_ids)
    except Exception as e:
        logger.warning("Failed to build collection overview: %s", e)
        collection_overview = "(Collection overview unavailable.)"

    expertise_block = _build_expertise_block(
        expertise_store, collection_id, search_col_ids,
    )

    system_text = _assemble_system_prompt(
        scope_note="all collections" if search_col_ids is not None else "the current collection",
        collection_overview=collection_overview,
        expertise_block=expertise_block,
        inline_block=inline_block,
        context_text=context_text,
        tables_block=describe_tables_for_prompt(tool_tables) if tool_tables else "",
    )

    messages = [
        {"role": m.role, "content": m.content}
        for m in chat_request.messages
    ]

    turn = ChatTurn(
        provider=provider,
        system_text=system_text,
        messages=messages,
        agent_context={"collection_id": collection_id, "scope": chat_request.scope},
        model=provider.QUALITY_MODEL,
        max_tokens=max_tokens,
        max_iterations=max_iterations,
    )

    return PreparedChat(
        turn=turn,
        filtered_results=filtered_results,
        rerank_usage=rerank_usage,
        has_executed_tools=bool(tool_tables),
    )


def _reformulate_if_needed(
    ai_service: AIService, chat_request: ChatRequest, latest_query: str,
) -> str:
    prior_messages = chat_request.messages[:-1]
    if not prior_messages:
        return latest_query
    history_for_reformulation = [
        {"role": m.role, "content": m.content} for m in prior_messages
    ]
    return ai_service.reformulate_query(history_for_reformulation, latest_query)


def _resolve_scope(scope: str, current_collection_id: str) -> list[str] | None:
    """Return the explicit list of collection ids to search, or None for single-collection."""
    if scope == "all":
        return [c["id"] for c in collection_service.get_all_collections()]
    if scope.startswith("group:"):
        from services.app_database import app_db
        group = app_db.get_collection_group(scope[6:])
        return group["collection_ids"] if group else [current_collection_id]
    return None


def _run_search(
    search_col_ids: list[str] | None,
    collection_id: str,
    search_query: str,
    chat_request: ChatRequest,
) -> tuple[list, list[str]]:
    if search_col_ids is None:
        indexer = indexer_manager.get_indexer(collection_id)
        result = indexer.search(
            query=search_query, top_k=chat_request.top_k, mode=chat_request.mode,
        )
        return result["results"], [collection_id] * len(result["results"])

    context_results: list = []
    result_collection_ids: list[str] = []
    for col_id in search_col_ids:
        try:
            col_indexer = indexer_manager.get_indexer(col_id)
            col_search = col_indexer.search(
                query=search_query, top_k=chat_request.top_k, mode=chat_request.mode,
            )
            for r in col_search["results"]:
                context_results.append(r)
                result_collection_ids.append(col_id)
        except Exception as e:
            logger.warning("Chat: search failed for collection '%s': %s", col_id, e)

    paired = sorted(
        zip(context_results, result_collection_ids),
        key=lambda x: x[0].similarity_score, reverse=True,
    )[:chat_request.top_k]
    return [p[0] for p in paired], [p[1] for p in paired]


def _rerank(
    ai_service: AIService,
    latest_query: str,
    context_results: list,
    result_collection_ids: list[str],
    top_k: int,
) -> tuple[list, list[str], dict[str, Any] | None]:
    try:
        rerank_input = [
            {
                "index": i,
                "filename": r.filename,
                "text_snippet": r.text_snippet,
                "similarity_score": r.similarity_score,
            }
            for i, r in enumerate(context_results)
        ]
        rerank_result = ai_service.rerank_results(latest_query, rerank_input, top_k)
        valid_indices = [
            i for i in rerank_result["reranked_indices"]
            if 0 <= i < len(context_results)
        ]
        if valid_indices:
            context_results = [context_results[i] for i in valid_indices]
            result_collection_ids = [result_collection_ids[i] for i in valid_indices]
        return context_results, result_collection_ids, rerank_result.get("usage")
    except Exception as e:
        logger.warning("Chat context reranking failed, using original order: %s", e)
        return context_results, result_collection_ids, None


def _format_retrieved_context(filtered_results: list[tuple[Any, str]]) -> str:
    if not filtered_results:
        return "No relevant context found."
    parts = [
        f"[Source {i + 1}: {r.filename}, page {r.page_number}]\n{r.text_snippet}"
        for i, (r, _cid) in enumerate(filtered_results)
    ]
    return "\n\n---\n\n".join(parts)


def _build_expertise_block(
    expertise_store: ExpertiseStore,
    collection_id: str,
    search_col_ids: list[str] | None,
) -> str:
    """Expertise packs are scoped to a single Collection; skip in 'all'/group scope."""
    if search_col_ids is not None:
        return ""
    try:
        attached_packs = expertise_store.get_packs_for_collection(collection_id)
    except Exception as e:
        logger.warning("Failed to load expertise packs for collection %s: %s", collection_id, e)
        return ""
    if not attached_packs:
        return ""
    sections = "\n\n".join(
        f"## {p.name}\n{(p.description + chr(10)) if p.description else ''}{p.body}".strip()
        for p in attached_packs
    )
    return (
        "ADVISOR EXPERTISE (apply these frameworks when analyzing this portfolio):\n"
        + sections
    )


def _assemble_system_prompt(
    *,
    scope_note: str,
    collection_overview: str,
    expertise_block: str,
    inline_block: str,
    context_text: str,
    tables_block: str,
) -> str:
    """Assemble the system prompt the engine will hand to the provider.

    The advisor-framing language is load-bearing — it positions the user as
    the advisor analysing a client's portfolio (third person), never as the
    investor. See feedback_advisor_framing memory for context.
    """
    base_parts = [
        f"You are an analytical assistant for a financial advisor. The person "
        f"chatting with you is the advisor — not the client. The documents, "
        f"holdings, accounts, and portfolio data in {scope_note} belong to "
        f"one of the advisor's clients.",
        "Always refer to the portfolio in the third person: \"the client's "
        "holdings\", \"the client's cash position\", \"this account\" — never "
        "\"your holdings\" or \"your portfolio\". Frame recommendations as "
        "observations and options the advisor can weigh, raise with the client, "
        "or act on in a professional capacity; do not address the advisor as if "
        "they were the investor.",
        "Answer the advisor's question using the COLLECTION OVERVIEW, STRUCTURED "
        "TABLES (when provided), and RETRIEVED CONTEXT below.",
        "Use the COLLECTION OVERVIEW for meta-questions about the knowledge base "
        "itself (file counts, available documents, date ranges).",
    ]
    if inline_block:
        base_parts.append(
            "When STRUCTURED TABLES are included below, they are the FULL contents "
            "of CSV/XLSX files as JSONL — every row is present. For any numeric, "
            "sum, count, average, filter, date-range, or ranking question about "
            "those files, answer DIRECTLY from the JSONL rows and show your "
            "arithmetic. Do NOT guess from chunk snippets and do NOT assume data "
            "is missing."
        )
    if tables_block:
        base_parts.append(
            "For questions about the LARGE tables listed under TOOL USE PROTOCOL, "
            "call the structured-query tools — do not estimate from row text."
        )
    if expertise_block:
        base_parts.append(
            "When ADVISOR EXPERTISE is provided, follow its guidance, rules, and "
            "frameworks as authoritative instructions for this analysis."
        )
    base_parts.append(
        "Use RETRIEVED CONTEXT for prose/document questions, citing [Source N] "
        "as needed."
    )

    sections = [" ".join(base_parts), f"COLLECTION OVERVIEW:\n{collection_overview}"]
    if expertise_block:
        sections.append(expertise_block)
    if inline_block:
        sections.append(inline_block)
    if context_text and context_text != "No relevant context found.":
        sections.append(f"PRE-RETRIEVED CONTEXT (optional primer):\n{context_text}")
    if tables_block:
        sections.append(f"LARGE TABLES AVAILABLE:\n{tables_block}")
    return "\n\n".join(sections)
