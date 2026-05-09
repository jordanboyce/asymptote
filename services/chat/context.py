"""Chat-specific context builder.

Turns a ChatRequest + provider into a prepared ChatTurn (system text,
messages, agent context) plus the filtered search results the endpoint
needs to build its source list.

Chat is always scoped to a single Collection (the household). The advisor
can further restrict to a subset of sources via the SourcesSidebar
checkboxes — that becomes `chat_request.document_ids`. Cross-collection
queries are search's job, not chat's; see /search and SearchTab.

Phases (in order):

  1. Reformulate the latest user message using prior history (skipped on
     single-turn queries to avoid an unnecessary LLM call).
  2. Search the current collection (filtered by document_ids if the
     advisor narrowed scope).
  3. Optionally rerank the results.
  4. Collect Holdings tables for the collection — small ones get inlined
     as JSONL, large ones are exposed as tool-callable tables.
  5. Filter out chunks whose files were inlined (the JSONL is authoritative).
  6. Assemble the system prompt: advisor framing + Collection overview +
     Expertise packs + inline JSONL block + LARGE TABLES block +
     RETRIEVED CONTEXT.

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
    # Search results that survived the inlining-filter. The endpoint uses
    # these to build pdf_url / page_url entries against the chat's
    # collection_id — URLs aren't the engine's concern.
    filtered_results: list[Any]
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

    # Per-document scope filter ("only chat about these N sources"). Empty list
    # is treated like None — see SourcesSidebar's "all checked" default state.
    doc_filter = set(chat_request.document_ids) if chat_request.document_ids else None

    search_query = _reformulate_if_needed(ai_service, chat_request, latest_query)
    context_results = _run_search(
        collection_id, search_query, chat_request, doc_filter,
    )

    rerank_usage = None
    if chat_request.rerank and context_results:
        context_results, rerank_usage = _rerank(
            ai_service, latest_query, context_results, chat_request.top_k,
        )

    structured_tables, structured_stores = collect_structured_tables([collection_id])
    if doc_filter is not None and structured_tables:
        structured_tables = [
            t for t in structured_tables if t.get("document_id") in doc_filter
        ]
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
        r for r in context_results if r.filename not in inlined_filenames
    ]

    context_text = _format_retrieved_context(filtered_results)

    try:
        collection_overview = build_collection_overview(
            [collection_id],
            document_ids=list(doc_filter) if doc_filter is not None else None,
        )
    except Exception as e:
        logger.warning("Failed to build collection overview: %s", e)
        collection_overview = "(Collection overview unavailable.)"

    expertise_block = _build_expertise_block(expertise_store, collection_id)

    system_text = _assemble_system_prompt(
        collection_overview=collection_overview,
        expertise_block=expertise_block,
        inline_block=inline_block,
        context_text=context_text,
        tables_block=describe_tables_for_prompt(tool_tables) if tool_tables else "",
        doc_filter_count=len(doc_filter) if doc_filter is not None else None,
    )

    messages = [
        {"role": m.role, "content": m.content}
        for m in chat_request.messages
    ]

    turn = ChatTurn(
        provider=provider,
        system_text=system_text,
        messages=messages,
        agent_context={"collection_id": collection_id},
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


def _run_search(
    collection_id: str,
    search_query: str,
    chat_request: ChatRequest,
    doc_filter: set[str] | None = None,
) -> list:
    # When a document filter is active we widen the fetch so the post-filter
    # pool can still satisfy top_k. Without this, narrowing scope to a single
    # doc could leave us with zero hits if the top_k unfiltered results all
    # came from other docs.
    fetch_k = chat_request.top_k * 5 if doc_filter else chat_request.top_k
    indexer = indexer_manager.get_indexer(collection_id)
    result = indexer.search(
        query=search_query, top_k=fetch_k, mode=chat_request.mode,
    )
    results = result["results"]
    if doc_filter is not None:
        results = [r for r in results if r.document_id in doc_filter]
    return results[:chat_request.top_k]


def _rerank(
    ai_service: AIService,
    latest_query: str,
    context_results: list,
    top_k: int,
) -> tuple[list, dict[str, Any] | None]:
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
        return context_results, rerank_result.get("usage")
    except Exception as e:
        logger.warning("Chat context reranking failed, using original order: %s", e)
        return context_results, result_collection_ids, None


def _format_retrieved_context(filtered_results: list) -> str:
    if not filtered_results:
        return "No relevant context found."
    parts = [
        f"[Source {i + 1}: {r.filename}, page {r.page_number}]\n{r.text_snippet}"
        for i, r in enumerate(filtered_results)
    ]
    return "\n\n---\n\n".join(parts)


def _build_expertise_block(
    expertise_store: ExpertiseStore,
    collection_id: str,
) -> str:
    """Expertise packs are attached to the active Collection."""
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
    collection_overview: str,
    expertise_block: str,
    inline_block: str,
    context_text: str,
    tables_block: str,
    doc_filter_count: int | None = None,
) -> str:
    """Assemble the system prompt the engine will hand to the provider.

    The advisor-framing language is load-bearing — it positions the user as
    the advisor analysing a client's portfolio (third person), never as the
    investor. See feedback_advisor_framing memory for context.
    """
    base_parts = [
        f"You are an analytical assistant for a financial advisor. The person "
        "chatting with you is the advisor — not the client. The documents, "
        "holdings, accounts, and portfolio data in this collection belong to "
        "one of the advisor's clients.",
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
        # R5.6 — when the data needed to answer is genuinely absent, refuse to
        # speculate. The advisor must be able to trust that "I don't see X"
        # means "X isn't in this collection," not "I didn't try hard enough."
        "If the question cannot be answered from the data in this collection — "
        "for example, the COLLECTION OVERVIEW shows no relevant documents, the "
        "STRUCTURED TABLES lack the column the question requires, or a tool "
        "call returns no rows — say so explicitly. Begin the answer with "
        "\"I don't have …\" and name the missing piece (e.g. \"I don't have "
        "cost-basis data for this collection\", \"I don't have account-level "
        "tagging in this export\", \"I don't have any holdings files indexed "
        "yet\"). Do NOT guess, do NOT estimate from chunk text, and do NOT "
        "invent positions, account numbers, or dollar figures. Suggest the "
        "next concrete step the advisor could take (upload a different "
        "export, switch collections, etc.) only when one is obvious.",
    ]
    if doc_filter_count is not None:
        # Tell the model the narrowed scope is intentional — otherwise it may
        # hedge ("I don't see any other documents…") as if data is missing.
        plural = "source" if doc_filter_count == 1 else "sources"
        base_parts.append(
            f"The advisor has restricted this conversation to {doc_filter_count} "
            f"specific {plural}. The COLLECTION OVERVIEW, STRUCTURED TABLES, and "
            f"RETRIEVED CONTEXT below already reflect that filter — answer only "
            f"from these sources and do not speculate about documents that have "
            f"been excluded from scope."
        )
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
