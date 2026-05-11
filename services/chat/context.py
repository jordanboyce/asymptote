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
from services.ai_service import AIProvider, AIService, AnthropicProvider
from services.chat.engine import ChatTurn
from services.collection_context import (
    detect_collection_kind,
    system_prompt_addendum,
    tools_for_kind,
)
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

    # On Anthropic, retrieved chunks ride on the user turn as native-citation
    # ``document`` blocks so the model can emit structured citations the
    # frontend deep-links from. Other providers don't have a citations API,
    # so they keep the legacy prose RETRIEVED CONTEXT block in the system
    # prompt (with [Source N] tags) — see fallback note in the v4.4.4 spec.
    use_native_citations = isinstance(provider, AnthropicProvider)
    if use_native_citations:
        documents, document_metadata = _build_citation_documents(filtered_results)
        context_text = ""  # documents replace the prose block
    else:
        documents, document_metadata = [], []
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

    # Detect what kind of Collection this is (financial / meetings / general
    # / mixed) so the engine can filter the advertised tool list and the
    # system prompt can carry a one-paragraph "this collection looks like X"
    # hint. Deterministic SQL inspection — no LLM call, safe per turn.
    try:
        indexer = indexer_manager.get_indexer(collection_id)
        collection_ctx = detect_collection_kind(indexer, collection_id)
    except Exception as e:
        logger.warning(
            "Collection-kind detection failed for %s, defaulting to general: %s",
            collection_id, e,
        )
        from services.collection_context import CollectionContext
        collection_ctx = CollectionContext(
            collection_id=collection_id,
            kind="general",
            financial_table_count=0,
            transcript_count=0,
            document_count=0,
        )
    allowed_tool_names = tools_for_kind(collection_ctx.kind)
    context_addendum = system_prompt_addendum(collection_ctx)

    system_text = _assemble_system_prompt(
        collection_overview=collection_overview,
        expertise_block=expertise_block,
        inline_block=inline_block,
        context_text=context_text,
        tables_block=describe_tables_for_prompt(tool_tables) if tool_tables else "",
        doc_filter_count=len(doc_filter) if doc_filter is not None else None,
        native_citations=use_native_citations and bool(documents),
        context_addendum=context_addendum,
        kind=collection_ctx.kind,
    )

    messages = [
        {"role": m.role, "content": m.content}
        for m in chat_request.messages
    ]

    turn = ChatTurn(
        provider=provider,
        system_text=system_text,
        messages=messages,
        agent_context={"collection_id": collection_id, "kind": collection_ctx.kind},
        model=provider.QUALITY_MODEL,
        max_tokens=max_tokens,
        max_iterations=max_iterations,
        documents=documents,
        document_metadata=document_metadata,
        extended_thinking=bool(getattr(chat_request, "extended_thinking", False)),
        allowed_tool_names=allowed_tool_names,
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


def _build_citation_documents(filtered_results: list) -> tuple[list[dict], list[dict]]:
    """Build Anthropic-native ``document`` content blocks plus a parallel
    metadata list keyed by ``document_index``.

    One block per retrieved chunk — Anthropic returns ``char_location``
    citations against each block independently, so per-chunk granularity
    is what makes the inline pills click-through to the right snippet.
    """
    documents: list[dict] = []
    metadata: list[dict] = []
    for r in filtered_results:
        text = r.text_snippet or ""
        if not text:
            continue
        title = f"{r.filename} — p.{r.page_number}"
        documents.append({
            "type": "document",
            "source": {
                "type": "text",
                "media_type": "text/plain",
                "data": text,
            },
            "title": title,
            "citations": {"enabled": True},
        })
        metadata.append({
            "document_id": r.document_id,
            "chunk_id": getattr(r, "chunk_id", None),
            "page_number": r.page_number,
            "filename": r.filename,
        })
    return documents, metadata


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


def _build_base_framing(
    *,
    is_financial: bool,
    native_citations: bool,
) -> list[str]:
    """The opening few sentences of the system prompt — POV + grounding rules.

    Two modes share the same shape:

      - **Financial/mixed:** advisor-POV. The user is the advisor analysing a
        client's portfolio; references to holdings stay in the third person.
        This is the framing Finn shipped with for the v4.4.x advisor pivot.
      - **General/meetings:** neutral document-analyst POV — the user is just
        chatting with their own documents. Second-person fine, no portfolio
        framing. This is Finn's original use case (chat with documents)
        which the financial-services pivot must not break.

    Both modes share the "don't speculate when data is missing" rule and the
    "use COLLECTION OVERVIEW for meta-questions" rule, with the actor noun
    swapped (advisor → user) so the prose reads naturally.
    """
    if is_financial:
        retrieved_phrase = (
            "Answer the advisor's question using the COLLECTION OVERVIEW, "
            "STRUCTURED TABLES (when provided), and "
            + ("the source documents attached to the user turn."
               if native_citations
               else "RETRIEVED CONTEXT below.")
        )
        return [
            "You are an analytical assistant for a financial advisor. The person "
            "chatting with you is the advisor — not the client. The documents, "
            "holdings, accounts, and portfolio data in this collection belong to "
            "one of the advisor's clients.",
            "Always refer to the portfolio in the third person: \"the client's "
            "holdings\", \"the client's cash position\", \"this account\" — never "
            "\"your holdings\" or \"your portfolio\". Frame recommendations as "
            "observations and options the advisor can weigh, raise with the client, "
            "or act on in a professional capacity; do not address the advisor as if "
            "they were the investor.",
            retrieved_phrase,
            "Use the COLLECTION OVERVIEW for meta-questions about the knowledge base "
            "itself (file counts, available documents, date ranges).",
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
    # General / meetings — chat with documents. Neutral framing, no portfolio
    # pitch, no advisor-vs-client distinction.
    retrieved_phrase = (
        "Answer the user's question using the COLLECTION OVERVIEW and "
        + ("the source documents attached to the user turn."
           if native_citations
           else "RETRIEVED CONTEXT below.")
    )
    return [
        "You are a helpful assistant that helps the user understand and reason "
        "about the documents in this collection. Treat the collection as the "
        "user's own working set of files — research notes, contracts, papers, "
        "manuals, transcripts, anything they have uploaded — and answer their "
        "questions about what is in it.",
        retrieved_phrase,
        "Use the COLLECTION OVERVIEW for meta-questions about the knowledge base "
        "itself (file counts, available documents, date ranges).",
        "If the question cannot be answered from the documents in this collection "
        "— for example, the COLLECTION OVERVIEW shows no relevant documents or "
        "a search returns no matches — say so explicitly. Begin the answer with "
        "\"I don't have …\" or \"I couldn't find …\" and name the missing piece. "
        "Do NOT guess, do NOT invent facts, dates, or names that aren't in the "
        "documents, and do NOT fill in plausible-sounding detail to sound "
        "complete. Suggest the next concrete step (upload a different document, "
        "rephrase the question) only when one is obvious.",
    ]


def _assemble_system_prompt(
    *,
    collection_overview: str,
    expertise_block: str,
    inline_block: str,
    context_text: str,
    tables_block: str,
    doc_filter_count: int | None = None,
    native_citations: bool = False,
    context_addendum: str = "",
    kind: str = "general",
) -> str:
    """Assemble the system prompt the engine will hand to the provider.

    Branches on the detected Collection ``kind``:

      - **financial / mixed** — the advisor-POV framing is load-bearing: the
        model treats the user as the advisor analysing a client's portfolio
        (third person, never "your holdings"), and the wash-sale reminder
        is in scope. See feedback_advisor_framing memory.
      - **general / meetings** — the user is chatting with their own
        documents; the advisor framing would be wrong (and confusing). The
        prompt becomes a neutral document-analyst framing — second person
        is fine, no portfolio/wash-sale pitch.

    When ``native_citations`` is true, retrieved chunks ride on the user turn
    as Anthropic ``document`` blocks (with citations enabled) instead of being
    pasted into RETRIEVED CONTEXT — the model emits structured ``citations``
    blocks pointing back at the source spans.
    """
    is_financial = kind in ("financial", "mixed")
    base_parts = _build_base_framing(
        is_financial=is_financial,
        native_citations=native_citations,
    )
    if is_financial:
        # The wash-sale reminder + find_tax_loss_candidates pointer only make
        # sense when the LLM actually has those tools and the collection
        # actually has holdings to discuss.
        base_parts.append(
            "When discussing tax-loss harvesting, the wash-sale rule applies "
            "across the entire household — including spousal IRA, Roth, and "
            "401(k) accounts — and harvesting RESETS cost basis (defers tax, "
            "does not eliminate it). Use the find_tax_loss_candidates tool when "
            "the advisor asks for harvest candidates; it composes the household-"
            "wide wash-sale check with replacement-ETF suggestions."
        )
    if doc_filter_count is not None:
        # Tell the model the narrowed scope is intentional — otherwise it may
        # hedge ("I don't see any other documents…") as if data is missing.
        plural = "source" if doc_filter_count == 1 else "sources"
        actor = "advisor" if is_financial else "user"
        base_parts.append(
            f"The {actor} has restricted this conversation to {doc_filter_count} "
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
    if native_citations:
        # The Anthropic citations API wires up clickable pills for spans the
        # model quotes verbatim — encourage direct quotation of the source so
        # users get inline citations they can deep-link from.
        verifier = "advisor" if is_financial else "user"
        base_parts.append(
            "For prose/document questions, draw on the source documents "
            "attached to the user turn. Quote relevant spans directly so the "
            f"answer carries inline citations the {verifier} can verify."
        )
    else:
        base_parts.append(
            "Use RETRIEVED CONTEXT for prose/document questions, citing [Source N] "
            "as needed."
        )

    sections = [" ".join(base_parts), f"COLLECTION OVERVIEW:\n{collection_overview}"]
    if context_addendum:
        sections.append(f"COLLECTION KIND:\n{context_addendum}")
    if expertise_block:
        sections.append(expertise_block)
    if inline_block:
        sections.append(inline_block)
    if context_text and context_text != "No relevant context found.":
        sections.append(f"PRE-RETRIEVED CONTEXT (optional primer):\n{context_text}")
    if tables_block:
        sections.append(f"LARGE TABLES AVAILABLE:\n{tables_block}")
    return "\n\n".join(sections)
