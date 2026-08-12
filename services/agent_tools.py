"""JSON-Schema tool specs for the agentic chat loop.

One canonical list of tools. Each provider's `complete_with_tools` consumes
either `anthropic_tools()` or `openai_tools()` — the underlying dispatch is
shared via `services.structured_chat.execute_tool_calls`.
"""

from __future__ import annotations

from typing import Any, Dict, List


# Canonical tool list. `parameters` is JSON Schema (draft-7-compatible).
_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_documents",
        "description": (
            "Semantic / keyword / hybrid search across the active document "
            "collection. Use for narrative, prose, or conceptual questions "
            "about PDFs, text, or code. Returns ranked chunks with filename, "
            "page number, and a similarity score."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language query or exact keywords."},
                "mode": {"type": "string", "enum": ["semantic", "keyword", "hybrid"]},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "collection_id": {"type": "string", "description": "Omit to use the active chat collection."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document_context",
        "description": (
            "Fetch the full text of a document, a page, or a chunk neighborhood. "
            "Use after search_documents when you need more than the truncated excerpt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "page_number": {"type": "integer"},
                "chunk_id": {"type": "string"},
                "window": {"type": "integer", "description": "Chunks before/after when chunk_id is given."},
                "max_chars": {"type": "integer"},
                "collection_id": {"type": "string"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "list_tables",
        "description": (
            "List every CSV/Excel sheet ingested as a typed SQL table. Call this "
            "first when a question requires numeric/aggregation/filtering/ranking "
            "over tabular data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
            },
        },
    },
    {
        "name": "get_table_schema",
        "description": (
            "Return the full typed schema of an ingested CSV/Excel sheet, "
            "including column sql_names, types, detected financial roles, "
            "sample values, and stats."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "table_name, filename, or document_id."},
                "collection_id": {"type": "string"},
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "get_table_rows",
        "description": (
            "Return the full rows of an ingested table in one call (no SQL). "
            "Best for small/medium tables up to ~2000 rows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                "collection_id": {"type": "string"},
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "query_table",
        "description": (
            "Run a read-only SQL SELECT against the typed CSV/Excel tables. "
            "Rules: SELECT/WITH only, single statement, double-quote every "
            "identifier (column names are case-sensitive — use the exact sql_name)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 2000},
                "collection_id": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "aggregate_table",
        "description": "Group-by aggregation on a table without writing SQL.",
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string"},
                "aggregate_col": {"type": "string"},
                "agg_fn": {"type": "string", "enum": ["sum", "mean", "count", "min", "max"]},
                "group_by": {"type": "string"},
                "sort_by": {"type": "string", "enum": ["value_asc", "value_desc", "group_asc", "group_desc"]},
                "limit": {"type": "integer"},
                "collection_id": {"type": "string"},
            },
            "required": ["identifier", "aggregate_col", "agg_fn"],
        },
    },
    {
        "name": "compute_portfolio_metric",
        "description": (
            "Run a canned financial portfolio metric. Only valid when "
            "get_table_schema reports non-empty financial_roles. Supported: "
            "row_count, total_market_value, total_cost_basis, total_pnl, "
            "top_holdings, bottom_holdings, largest_gains, largest_losses, "
            "concentration, breakdown_by_sector, breakdown_by_asset_class, "
            "breakdown_by_region, breakdown_by_currency, weighted_return, "
            "summary_statistics."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string"},
                "metric": {"type": "string"},
                "limit": {"type": "integer"},
                "group_by_symbol": {"type": "boolean"},
                "collection_id": {"type": "string"},
            },
            "required": ["identifier", "metric"],
        },
    },
    {
        "name": "prep_for_meeting",
        "description": (
            "Assemble the complete pre-meeting page for a client. START HERE "
            "for \"prep me for the meeting with X\", \"what do I need to know "
            "before this review?\", \"what should I raise?\". One call returns: "
            "what was discussed and left open last time, aged action items "
            "with overdue flags, portfolio totals and top positions, drift "
            "against the client's IPS target allocation, positions above their "
            "concentration ceiling, prohibited-holding matches, harvestable "
            "losses with household wash-sale checks, corporate events under "
            "the top holdings, and a priority-ordered agenda. Fully computed "
            "— no LLM call, so the same data always yields the same page. "
            "The response carries a `gaps` array naming everything prep could "
            "NOT determine; read it and never present a section as complete "
            "when a gap contradicts it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string", "description": "Omit to use the active chat collection."},
                "when": {"type": "string", "description": "ISO timestamp of the meeting. Drives 'days since' and overdue arithmetic. Defaults to now."},
                "household_collection_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra collections for wash-sale scope only (e.g. a spouse's separately-titled account).",
                },
                "include_tlh": {"type": "boolean", "description": "Include the tax-loss scan. Default true."},
                "include_market_context": {"type": "boolean", "description": "Include corporate events on top holdings. Default true."},
                "top_n": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "get_client_profile",
        "description": (
            "Return the client's profile / Investment Policy Statement: risk "
            "tolerance, time horizon, goals, household members, IPS target "
            "allocation and rebalance bands, max single position, prohibited "
            "holdings, tax bracket, and liquidity needs. Call this before "
            "judging a portfolio 'concentrated', 'overweight equities', "
            "'holding too much cash', or 'due for a rebalance' — those "
            "judgments are meaningless without the client's own targets. "
            "When `exists` is false, say the policy is unset rather than "
            "applying a generic rule of thumb as if it were the client's."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string", "description": "Omit to use the active chat collection."},
            },
        },
    },
    {
        "name": "list_collections",
        "description": "List every available collection (id, name, document count).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_collection_info",
        "description": "Full document listing for a collection.",
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
            },
        },
    },
    {
        "name": "get_price_history",
        "description": "Historical OHLCV price data for a ticker symbol via Yahoo Finance.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "period": {"type": "string", "description": "e.g. '1d', '5d', '1mo', '6mo', '1y', '5y', 'max'."},
                "interval": {"type": "string", "description": "e.g. '1d', '1wk', '1mo'."},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_security_classification",
        "description": "Sector, market cap, and asset-class classification for a ticker symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_company_profile",
        "description": (
            "Company-level metadata for a ticker via Yahoo Finance: current "
            "CEO and other officers, business summary, sector, industry, "
            "website, employee count, market cap. Use for \"who is the CEO\", "
            "\"what does X do\", \"where are they headquartered\" questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_company_news",
        "description": (
            "Recent news headlines for a ticker from Yahoo Finance. Use for "
            "\"any recent news / updates / announcements / CEO changes / "
            "guidance changes / M&A activity on X\" questions. Returns "
            "articles with title, publisher, publish time, and URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["symbol"],
        },
    },
]


def anthropic_tools() -> List[Dict[str, Any]]:
    """Shape tools for Anthropic's messages API (`tools` parameter)."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in _TOOLS
    ]


def openai_tools() -> List[Dict[str, Any]]:
    """Shape tools for OpenAI's chat.completions API (`tools` parameter)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in _TOOLS
    ]


def tool_names() -> List[str]:
    return [t["name"] for t in _TOOLS]
