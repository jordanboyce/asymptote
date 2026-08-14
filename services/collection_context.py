"""Per-Collection context detection.

Classifies a Collection into one of four kinds based on what's actually
indexed — no LLM call, just deterministic counts against the metadata.db.
The chat engine uses the kind to decide which tools to advertise to the
LLM and what one-paragraph addendum to append to the system prompt.

Why this matters: when a Collection has zero CSVs/XLSXs with detected
financial roles (e.g. a compliance binder of PDFs and Word docs), telling
the LLM about ``compute_portfolio_metric``, ``query_table``, and the four
market-data tools wastes input tokens and tempts the model into calling
them anyway. The fix is to *not advertise* what doesn't apply.

The classification is intentionally additive:

  - **financial**  — has at least one CSV/XLSX/PDF table with detected
                     financial roles
  - **meetings**   — has at least one transcript (extraction_method =
                     'whisper'), and no financial-role tables
  - **mixed**      — has both
  - **general**    — anything else (compliance docs, code, free-form
                     text). Search + get_document_context are always
                     useful here; nothing else applies.

A Collection with no documents at all is also ``general`` — the empty
state. Chat will tell the advisor the collection is empty rather than
trying to call tools that won't return anything.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# Tool names match the canonical list in :mod:`services.agent_tools` and the
# MCP registrations in :mod:`services.mcp_server`. Keep these in sync when a
# new tool ships.

# Tools that apply to *every* collection — search, fetch, and the meta
# navigation tools. These never come off the menu, regardless of kind.
_BASE_TOOLS: frozenset[str] = frozenset({
    "search_documents",
    "get_document_context",
    "list_collections",
    "get_collection_info",
})

# Tools that only make sense when the Collection has typed brokerage tables
# with detected financial roles. ``query_table`` / ``aggregate_table`` /
# ``get_table_rows`` / ``get_table_schema`` work on *any* typed table, but
# without a financial-role table there's nothing for the model to call them
# *against* — and offering them up just invites speculative calls.
_FINANCIAL_TOOLS: frozenset[str] = frozenset({
    "list_tables",
    "get_table_schema",
    "get_table_rows",
    "query_table",
    "aggregate_table",
    "compute_portfolio_metric",
    "get_price_history",
    "get_security_classification",
    "get_company_profile",
    "get_company_news",
})

# The advisor-workflow composites. Each one reads the typed holdings tables,
# so they ride with _FINANCIAL_TOOLS rather than standing alone — offering
# `find_tax_loss_candidates` on a collection with no positions only invites a
# speculative call that comes back empty.
_ADVISORY_TOOLS: frozenset[str] = frozenset({
    "prep_for_meeting",
    "get_client_profile",
    "find_tax_loss_candidates",
    "generate_meeting_brief",
})

# Driven by extracted meeting notes rather than by holdings.
_MEETING_TOOLS: frozenset[str] = frozenset({
    "get_meeting_notes",
    "list_action_items",
})


# A table counts as "financial" only if it carries at least one Position-
# defining role — ticker/cusip/isin or a holdings measure or an account
# segmentation. Generic typed tables (PRA rule catalogs, log exports, code
# metrics) also live in the HoldingsStore and may have date/notes roles
# assigned, but they shouldn't tip the Collection into kind="financial".
# This is the single source of truth used by:
#   - detect_collection_kind below (chat tool gating)
#   - services.collection_summary (Studio Positions counter)
#   - services.mcp_server.search_collection (search-result auto-inline)
#   - services.chat.context.build_chat_inputs (system-prompt auto-inline)
HOLDINGS_ROLES: frozenset[str] = frozenset({
    "ticker", "cusip", "isin",
    "market_value", "quantity", "cost_basis",
    "unrealized_gain", "unrealized_loss",
    "account",
})


def is_holdings_table(roles: dict | None) -> bool:
    """True iff *roles* contains at least one Position-defining role.

    Pass either a per-column ``{sql_name: role}`` dict (the canonical shape
    on ``store.list_tables()`` entries) or any other dict whose values are
    role strings — only the value side is consulted.
    """
    if not roles:
        return False
    return any(role in HOLDINGS_ROLES for role in roles.values())


# Internal aliases kept for the existing call site in this module. New code
# should use the public names above.
_HOLDINGS_ROLES = HOLDINGS_ROLES
_is_holdings_table = is_holdings_table


CollectionKind = str  # Literal["financial", "meetings", "general", "mixed"]


@dataclass(frozen=True)
class CollectionContext:
    """Detected facts about a Collection — what's in it and what tools apply."""

    collection_id: str
    kind: CollectionKind
    financial_table_count: int
    transcript_count: int
    document_count: int

    def __bool__(self) -> bool:  # pragma: no cover — convenience only
        return self.document_count > 0


def detect_collection_kind(indexer: Any, collection_id: str) -> CollectionContext:
    """Inspect what's indexed for *collection_id* and classify the Collection.

    Pure read-against-SQLite — no LLM call, safe to invoke per chat turn.
    Falls back to ``general`` on any unexpected error rather than raising
    (a misclassification just means slightly noisier prompt; an exception
    here would kill the turn).
    """
    try:
        store = indexer.vector_store.holdings_store
    except AttributeError:
        store = None

    financial_table_count = 0
    if store is not None:
        try:
            for table in store.list_tables():
                roles = table.get("financial_roles") or {}
                if _is_holdings_table(roles):
                    financial_table_count += 1
        except Exception as exc:
            logger.debug(
                "Collection %s: holdings_store inspection failed (%s); "
                "falling back to general", collection_id, exc,
            )

    transcript_count, document_count = _count_transcripts_and_documents(
        indexer, collection_id,
    )

    if financial_table_count > 0 and transcript_count > 0:
        kind: CollectionKind = "mixed"
    elif financial_table_count > 0:
        kind = "financial"
    elif transcript_count > 0:
        kind = "meetings"
    else:
        kind = "general"

    return CollectionContext(
        collection_id=collection_id,
        kind=kind,
        financial_table_count=financial_table_count,
        transcript_count=transcript_count,
        document_count=document_count,
    )


def _count_transcripts_and_documents(
    indexer: Any, collection_id: str,
) -> tuple[int, int]:
    """Read directly from the per-Collection metadata.db.

    Avoids extending MetadataStore's API for a one-off count. The query is
    cheap (single grouped scan) and the database is already open via the
    indexer's VectorStore.
    """
    try:
        db_path = indexer.vector_store.metadata_db_path
    except AttributeError:
        return 0, 0

    try:
        uri = f"file:{db_path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]
            transcripts = conn.execute(
                "SELECT COUNT(*) FROM documents "
                "WHERE LOWER(COALESCE(extraction_method, '')) = 'whisper'"
            ).fetchone()[0]
            return int(transcripts or 0), int(total or 0)
    except sqlite3.OperationalError as exc:
        # First-turn-after-collection-creation: the documents table may not
        # have rows yet but the schema exists. A missing table would be a
        # different error path — log and degrade.
        logger.debug(
            "Collection %s: document count query failed (%s)",
            collection_id, exc,
        )
        return 0, 0
    except Exception as exc:
        logger.debug(
            "Collection %s: document count failed unexpectedly (%s)",
            collection_id, exc,
        )
        return 0, 0


def tools_for_kind(kind: CollectionKind) -> frozenset[str]:
    """Return the set of tool names the chat agent should advertise for *kind*.

    Returns a frozenset so callers can intersect cheaply and so the contract
    is "read-only" — kinds are not allowed to *exclude* base tools, only to
    add to them.
    """
    if kind == "financial":
        return _BASE_TOOLS | _FINANCIAL_TOOLS | _ADVISORY_TOOLS
    if kind == "mixed":
        return _BASE_TOOLS | _FINANCIAL_TOOLS | _ADVISORY_TOOLS | _MEETING_TOOLS
    if kind == "meetings":
        # No holdings here, so the portfolio side of the loop stays off the
        # menu — except `prep_for_meeting`, which is built to degrade: it
        # returns the meeting history and records the missing portfolio as a
        # gap rather than failing. That is the honest answer to "prep me for
        # this meeting" on a transcripts-only collection.
        return _BASE_TOOLS | _MEETING_TOOLS | frozenset({"prep_for_meeting"})
    # general — nothing domain-specific to call.
    return _BASE_TOOLS


def filter_tool_specs(
    tool_specs: Iterable[dict[str, Any]],
    *,
    allowed: Iterable[str],
) -> list[dict[str, Any]]:
    """Filter an Anthropic- or OpenAI-shaped tool spec list down to *allowed*.

    Handles both shapes:
      - Anthropic: ``{"name": "...", "description": "...", "input_schema": {...}}``
      - OpenAI:    ``{"type": "function", "function": {"name": "...", ...}}``
    """
    allowed_set = set(allowed)
    out: list[dict[str, Any]] = []
    for spec in tool_specs:
        name = spec.get("name")
        if name is None and spec.get("type") == "function":
            name = (spec.get("function") or {}).get("name")
        if name in allowed_set:
            out.append(spec)
    return out


def system_prompt_addendum(context: CollectionContext) -> str:
    """One-paragraph hint appended to the chat system prompt.

    Tells the LLM what *kind* of Collection it's working in so it picks
    appropriate tools and reframes its answers (no portfolio analytics
    pitch on a compliance binder; no "I can't find an IPS" on an empty
    collection that's clearly a brokerage export).
    """
    if context.kind == "financial":
        return (
            "This collection is a FINANCIAL collection — it contains "
            f"{context.financial_table_count} brokerage table(s) with detected "
            "holdings columns. Prefer the structured-query tools "
            "(list_tables / query_table / compute_portfolio_metric / "
            "aggregate_table) for any numeric, ranking, or aggregation "
            "question. Use search_documents only for prose context "
            "(disclosures, custodian letters, statements of investment "
            "policy)."
        )
    if context.kind == "meetings":
        return (
            "This collection is a MEETINGS collection — it contains "
            f"{context.transcript_count} audio transcript(s) and no brokerage "
            "data. Use search_documents to answer content questions about "
            "what was said in the meetings."
        )
    if context.kind == "mixed":
        return (
            "This collection contains BOTH brokerage data and meeting "
            f"transcripts ({context.financial_table_count} table(s), "
            f"{context.transcript_count} transcript(s)). Use the structured-"
            "query tools for numeric/holdings questions and search_documents "
            "for what was said in meetings; cross-reference the two when "
            "the advisor asks about how a decision in a meeting played out "
            "in the portfolio."
        )
    # general — no holdings, no transcripts. Could be compliance docs,
    # code, free-form text, or just an empty collection. Persona-neutral
    # copy: the empty case can be hit on a brand-new install with no advisor
    # context at all, so don't push brokerage-specific suggestions.
    if context.document_count == 0:
        return (
            "This collection is EMPTY — no documents indexed yet. Tell the user "
            "nothing has been uploaded and suggest they add a file to get started."
        )
    return (
        f"This collection is a GENERAL document collection — {context.document_count} "
        "document(s), no detected brokerage holdings, no meeting transcripts. "
        "Use search_documents and get_document_context for content questions; "
        "answer concisely with direct quotes rather than narrative summaries, "
        "and skip any financial/advisor framing — this is plain document Q&A."
    )
