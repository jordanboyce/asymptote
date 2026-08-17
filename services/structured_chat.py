"""Helpers for wiring structured CSV/XLSX query tools into the chat loop.

The chat endpoint uses a provider-agnostic ReAct-style tool-use loop: we
inject table schemas + tool-use instructions into the prompt, parse
`<tool_call>{...}</tool_call>` blocks out of the model's response, execute
them against the per-Collection HoldingsStore, and feed the results back
on a subsequent pass.

This approach works across every AI provider the app supports (Anthropic,
OpenAI, Ollama, Grok, Google, GitHub Models, OpenAI-compatible) without
needing provider-specific tool-use APIs.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from services.financial.metrics import AVAILABLE_METRICS, compute_financial_metric
from services.financial.holdings_store import HoldingsStore
from services.tabular.sql_validation import SQLValidationError

logger = logging.getLogger(__name__)


_TOOL_CALL_RE = re.compile(
    r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
    re.DOTALL,
)

_MAX_STRUCTURED_ROWS_PER_TOOL = 200
_MAX_SCHEMA_PROMPT_COLS = 40

SUPPORTED_TOOLS = {
    "query_table",
    "compute_portfolio_metric",
    "search_documents",
    "get_document_context",
    "list_tables",
    "get_table_schema",
    "get_table_rows",
    "aggregate_table",
    "list_collections",
    "get_collection_info",
    "get_price_history",
    "get_security_classification",
    "get_company_profile",
    "get_company_news",
    "get_corporate_events",
    "enrich_holdings",
    "prep_for_meeting",
    "get_client_profile",
    "get_meeting_notes",
    "list_action_items",
    "find_tax_loss_candidates",
    "generate_meeting_brief",
}


def describe_tables_for_prompt(tables: List[Dict[str, Any]], max_tables: int = 20) -> str:
    """Render table schemas into a compact human-readable block for the prompt."""
    if not tables:
        return ""

    lines = [
        "You have access to the following typed SQL tables built from the user's "
        "CSV / Excel files. Use them for any numeric, aggregation, filtering, or "
        "ranking question — raw SQL over these tables is dramatically more "
        "accurate than reading row text snippets.",
        "",
    ]
    lines.append(
        "CRITICAL: every line below shows two distinct names. The quoted "
        "value after `TABLE` is the SQL identifier — use it verbatim in "
        "FROM clauses. The value after `source:` is the human-readable "
        "filename — pass it to `identifier=` on schema/rows/aggregate/metric "
        "tools, but NEVER use it as a SQL table name. Confusing the two "
        "produces \"no such table\" errors."
    )
    lines.append("")
    for t in tables[:max_tables]:
        sheet_suffix = f" (sheet: {t['sheet_name']})" if t.get('sheet_name') else ""
        lines.append(
            f"TABLE \"{t['table_name']}\"  — source: {t['filename']}{sheet_suffix}"
        )
        lines.append(f"  rows: {t['row_count']}, columns: {t['column_count']}")
        cols = t.get('columns', [])[:_MAX_SCHEMA_PROMPT_COLS]
        for c in cols:
            role = f" [role: {c['role']}]" if c.get('role') else ""
            lines.append(
                f"    - \"{c['sql_name']}\" ({c['type']}){role}"
                + (f"  original: {c['name']}" if c['name'] != c['sql_name'] else "")
            )
        if len(t.get('columns', [])) > _MAX_SCHEMA_PROMPT_COLS:
            lines.append(f"    ... ({len(t['columns']) - _MAX_SCHEMA_PROMPT_COLS} more columns)")
        lines.append("")
    if len(tables) > max_tables:
        lines.append(f"... and {len(tables) - max_tables} more tables (call "
                     f"list_tables in MCP to see all).")
    return "\n".join(lines)


def build_tool_use_instructions() -> str:
    """Render the agentic tool-use protocol for the system prompt.

    Describes every tool the chat loop can dispatch (document retrieval,
    structured tables, market data, collection listing). Provider-agnostic
    because we use ReAct-style `<tool_call>{...}</tool_call>` blocks rather
    than each provider's native tool-calling API.
    """
    return (
        "TOOL USE PROTOCOL:\n"
        "You are an agent. You may call tools to retrieve documents, run "
        "structured queries against ingested CSV/XLSX tables, look up market "
        "data, or enumerate collections. Call tools whenever the answer "
        "requires data you don't already have in this prompt — do not guess.\n\n"
        "CRITICAL: Do NOT narrate what you are about to do (\"Let me query...\", "
        "\"I'll look this up...\"). Either emit an actual <tool_call> block, "
        "OR write the final answer. Prose that only announces intent without "
        "an accompanying tool_call is a bug. If you need data, emit the "
        "tool_call block immediately with no preamble.\n\n"
        "To call a tool, emit a JSON block wrapped in <tool_call>...</tool_call> "
        "on its own line. You may emit multiple blocks in one turn to call "
        "tools in parallel. After tool results come back you'll get another "
        "turn to either call more tools or produce the final answer.\n\n"
        "DOCUMENT RETRIEVAL:\n"
        '  - search_documents — semantic / keyword / hybrid search of the '
        'collection. Use for narrative / prose / conceptual questions.\n'
        '    <tool_call>{"tool": "search_documents", "query": "Q3 revenue commentary", "mode": "semantic", "top_k": 5}</tool_call>\n'
        '  - get_document_context — fetch the full text of a document, page, '
        'or chunk neighborhood. Use after search_documents to read more than '
        'the truncated excerpt.\n'
        '    <tool_call>{"tool": "get_document_context", "document_id": "<uuid>", "page_number": 3, "window": 1}</tool_call>\n\n'
        "STRUCTURED TABLES (CSV / XLSX ingested as typed SQL tables):\n"
        '  - list_tables — enumerate available tables. Call FIRST when a '
        'question involves CSV/XLSX data you don\'t already see inlined.\n'
        '    <tool_call>{"tool": "list_tables"}</tool_call>\n'
        '  - get_table_schema — full typed schema (columns, types, '
        'financial roles, sample values, stats).\n'
        '    <tool_call>{"tool": "get_table_schema", "identifier": "portfolio.csv"}</tool_call>\n'
        '  - get_table_rows — return the full rows of a small/medium table '
        'in one call (no SQL needed).\n'
        '    <tool_call>{"tool": "get_table_rows", "identifier": "portfolio.csv", "limit": 200}</tool_call>\n'
        '  - aggregate_table — group-by aggregation without writing SQL.\n'
        '    <tool_call>{"tool": "aggregate_table", "identifier": "portfolio.csv", "aggregate_col": "market_value", "agg_fn": "sum", "group_by": "sector", "sort_by": "value_desc"}</tool_call>\n'
        '  - query_table — read-only SQL SELECT for ad-hoc analytics.\n'
        '    <tool_call>{"tool": "query_table", "sql": "SELECT \\"sector\\", SUM(\\"market_value\\") FROM \\"csv_data_abc\\" GROUP BY \\"sector\\""}</tool_call>\n'
        '    Rules: SELECT/WITH only, single statement, double-quote every '
        'identifier, column names are case-sensitive (use the exact sql_name). '
        'The FROM target MUST be the quoted SQL `table_name` from the schema '
        'block above (looks like `csv_data_*`) — never the source filename '
        '(`portfolio.csv`, `HBIL*.csv`, etc.). Filenames go to `identifier=` '
        'on the other tools, not into SQL.\n'
        '  - compute_portfolio_metric — canned financial metric. Only valid '
        'when get_table_schema reports financial_roles.\n'
        '    <tool_call>{"tool": "compute_portfolio_metric", "identifier": "portfolio.csv", "metric": "top_holdings", "limit": 5}</tool_call>\n'
        '    Metrics: row_count, total_market_value, total_cost_basis, '
        'total_pnl, top_holdings, bottom_holdings, largest_gains, '
        'largest_losses, concentration, breakdown_by_sector, '
        'breakdown_by_asset_class, breakdown_by_region, breakdown_by_currency, '
        'weighted_return, summary_statistics.\n\n'
        "CLIENT MEETINGS:\n"
        '  - prep_for_meeting — START HERE for "prep me for the meeting with X", '
        '"what do I need to know before this review?", "what should I raise?". '
        'One call returns the whole page: what was said and left open last '
        'time, aged action items, portfolio totals, drift against the client\'s '
        'IPS targets, concentration and prohibited-holding breaches, '
        'harvestable losses, corporate events under the top holdings, and a '
        'priority-ordered agenda. Computed, not generated — no model involved.\n'
        '    <tool_call>{"tool": "prep_for_meeting"}</tool_call>\n'
        '    <tool_call>{"tool": "prep_for_meeting", "when": "2026-08-20T15:00:00Z"}</tool_call>\n'
        '    The result carries a `gaps` array listing everything prep could '
        'NOT determine. Read it. If you are about to assert something a gap '
        'covers ("allocation looks fine", "no concentration issues"), say '
        'instead that it could not be checked and why. Presenting a section '
        'as complete when `gaps` contradicts it is the worst failure mode '
        'this tool has.\n'
        '  - get_client_profile — the client\'s stated policy: risk tolerance, '
        'time horizon, goals, IPS target allocation and bands, concentration '
        'ceiling, prohibited holdings, tax bracket, liquidity needs.\n'
        '    <tool_call>{"tool": "get_client_profile"}</tool_call>\n'
        '    Call this before judging a portfolio "concentrated", "overweight", '
        '"too much cash", or "due to rebalance" — those words mean nothing '
        'without the client\'s own targets. When `exists` is false, say the '
        'policy is unset rather than substituting a generic rule of thumb.\n'
        '  - get_meeting_notes — what was actually said in past meetings: '
        'concerns, decisions, follow-up questions, sentiment. Use for "what '
        'did we tell her last time?" / "what was decided in March?".\n'
        '    <tool_call>{"tool": "get_meeting_notes"}</tool_call>\n'
        '    Extracted from the transcript only. If something is not in the '
        'notes, say it was not recorded — do not infer it from the portfolio.\n'
        '  - list_action_items — the flat cross-meeting list of what is still '
        'open. Use for "what\'s outstanding for this client?" / "what did I '
        'promise to do?".\n'
        '    <tool_call>{"tool": "list_action_items"}</tool_call>\n'
        '    <tool_call>{"tool": "list_action_items", "status": "all", "assignee": "advisor"}</tool_call>\n'
        '  - find_tax_loss_candidates — household-aware TLH plan. Taxable '
        'accounts only, short-term losses ranked first, each candidate paired '
        'with a non-substantially-identical replacement.\n'
        '    <tool_call>{"tool": "find_tax_loss_candidates"}</tool_call>\n'
        '    Always report the wash-sale flags alongside a candidate. A '
        'harvest presented without them is the error this tool exists to '
        'prevent — the symbol may be held in a spousal IRA you did not see.\n'
        '  - generate_meeting_brief — the portfolio-only brief (totals, '
        'accounts, top positions, concentration, cash drag, sectors). Prefer '
        'prep_for_meeting for meeting prep; use this when only the portfolio '
        'picture is wanted.\n'
        '    <tool_call>{"tool": "generate_meeting_brief"}</tool_call>\n'
        '    When a total comes back null with `total_market_value_reliable` '
        'false, the underlying column is stored as text and every sum of it is '
        'wrong. Say the total could not be verified and why. Do not reach into '
        'another section of the response for a substitute number.\n\n'
        "COLLECTION META:\n"
        '  - list_collections — list every available collection (id, name, '
        'doc count). Use when the user references a different client/project.\n'
        '    <tool_call>{"tool": "list_collections"}</tool_call>\n'
        '  - get_collection_info — full document listing for a collection.\n'
        '    <tool_call>{"tool": "get_collection_info", "collection_id": "..."}</tool_call>\n\n'
        "MARKET DATA:\n"
        '  - get_price_history — historical OHLCV for a ticker.\n'
        '    <tool_call>{"tool": "get_price_history", "symbol": "AAPL", "period": "1y"}</tool_call>\n'
        '  - get_security_classification — sector, market cap, asset class.\n'
        '    <tool_call>{"tool": "get_security_classification", "symbol": "AAPL"}</tool_call>\n'
        '  - get_company_profile — CEO, sector, employee count, business summary.\n'
        '    <tool_call>{"tool": "get_company_profile", "symbol": "AAPL"}</tool_call>\n'
        '  - get_company_news — recent headlines for a ticker.\n'
        '    <tool_call>{"tool": "get_company_news", "symbol": "AAPL", "limit": 5}</tool_call>\n'
        '  - get_corporate_events — SEC filings (8-K/10-K/10-Q, merger), '
        'dividends, splits, upcoming earnings since a date.\n'
        '    <tool_call>{"tool": "get_corporate_events", "symbol": "AAPL", "since": "2025-01-01", "types": ["8-K", "dividend"]}</tool_call>\n'
        '  - enrich_holdings — composite: pulls classification + company '
        'profile (optional events, price_1y) for every distinct ticker in '
        'a collection\'s holdings table. One call answers sector/growth-vs-'
        'value/CEO-changes questions at the portfolio level.\n'
        '    <tool_call>{"tool": "enrich_holdings", "collection_id": "...", "include": ["classification", "profile"]}</tool_call>\n\n'
        "OUTPUT FORMATTING:\n"
        "- Final answers are rendered as GitHub-flavored markdown. Use "
        "headings, bullets, bold, and tables when they help — but prefer "
        "tight prose for short answers.\n"
        "- When citing news from get_company_news, render every link as "
        "[Title](url) using the EXACT `url` field from the tool result. "
        "NEVER use `#`, `(here)`, or any placeholder href — if a result "
        "has no url, omit the link entirely. Same rule for any other "
        "tool that returns urls.\n\n"
        "GUIDANCE:\n"
        "- For numeric/aggregation/ranking questions about CSV/XLSX data, "
        "always go through the structured-table tools — never estimate from "
        "search excerpts.\n"
        "- For prose/conceptual questions about PDFs/docs, prefer "
        "search_documents (the prompt may already include retrieved chunks).\n"
        "- When you have everything you need, stop calling tools and write "
        "the final answer. Cite numbers verbatim from tool results; never "
        "invent values the tools didn't return.\n"
        "- Omit `collection_id` to use the active chat collection. Pass an "
        "explicit id only when the user asks about a different collection."
    )


def parse_tool_calls(response_text: str) -> List[Dict[str, Any]]:
    """Extract `<tool_call>...</tool_call>` JSON blocks from the model's output."""
    calls: List[Dict[str, Any]] = []
    for match in _TOOL_CALL_RE.finditer(response_text):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            calls.append({
                "tool": None,
                "raw": raw,
                "error": f"Invalid JSON in tool_call: {e}",
            })
            continue
        if not isinstance(obj, dict):
            calls.append({"tool": None, "raw": raw, "error": "tool_call must be a JSON object"})
            continue
        calls.append(obj)
    return calls


def _resolve_store_for_identifier(
    identifier: Optional[str],
    stores: Dict[str, HoldingsStore],
    tables: List[Dict[str, Any]],
) -> Optional[HoldingsStore]:
    """Look up the store whose tables contain the given identifier."""
    if not stores:
        return None
    if identifier:
        for t in tables:
            if identifier in (t.get("table_name"), t.get("filename"), t.get("document_id")):
                cid = t.get("collection_id")
                if cid and cid in stores:
                    return stores[cid]
    return next(iter(stores.values()))


def _resolve_store_for_sql(
    sql: str,
    stores: Dict[str, HoldingsStore],
    tables: List[Dict[str, Any]],
) -> Optional[HoldingsStore]:
    """Scan SQL text for a known table name and route to the owning store."""
    if not stores:
        return None
    for t in tables:
        name = t.get("table_name") or ""
        if name and re.search(rf'\b{re.escape(name)}\b', sql):
            cid = t.get("collection_id")
            if cid and cid in stores:
                return stores[cid]
    return next(iter(stores.values()))


_TOOL_ALIASES = {
    "query_structured_table": "query_table",
    "search": "search_documents",
    "search_collection": "search_documents",
    "document_context": "get_document_context",
}


def _coerce_collection_id(call: Dict[str, Any], default_collection_id: Optional[str]) -> Optional[str]:
    explicit = call.get("collection_id")
    if explicit:
        return explicit
    return default_collection_id


def execute_tool_calls(
    calls: List[Dict[str, Any]],
    agent_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Run each parsed tool call in-process via the MCP tool functions.

    `agent_context` carries the chat-level defaults the agent inherits when
    its tool call doesn't specify them — most importantly `collection_id`.
    All MCP wrappers apply Presidio redaction internally before returning,
    so results are safe to feed back into the AI prompt.
    """
    # Lazy import — services.mcp_server pulls in heavy deps and the agent
    # loop is the only consumer here.
    from services import mcp_server as mcp

    ctx = agent_context or {}
    default_collection_id = ctx.get("collection_id")

    results: List[Dict[str, Any]] = []
    for call in calls:
        if call.get("error"):
            results.append({
                "tool": call.get("tool"),
                "args": {"raw": call.get("raw")},
                "error": call["error"],
            })
            continue

        tool = call.get("tool")
        tool = _TOOL_ALIASES.get(tool, tool)
        if tool not in SUPPORTED_TOOLS:
            results.append({
                "tool": tool,
                "args": call,
                "error": f"Unknown tool '{tool}'. Supported: {sorted(SUPPORTED_TOOLS)}",
            })
            continue

        collection_id = _coerce_collection_id(call, default_collection_id)
        args_for_log: Dict[str, Any] = {}

        try:
            if tool == "search_documents":
                query = call.get("query")
                if not query:
                    raise ValueError("search_documents requires 'query'")
                mode = call.get("mode")
                top_k = call.get("top_k")
                args_for_log = {"query": query, "mode": mode, "top_k": top_k, "collection_id": collection_id}
                data = mcp.search_collection(
                    query=query,
                    collection_id=collection_id,
                    mode=mode,
                    top_k=int(top_k) if top_k is not None else None,
                )

            elif tool == "get_document_context":
                document_id = call.get("document_id")
                if not document_id:
                    raise ValueError("get_document_context requires 'document_id'")
                args_for_log = {
                    "document_id": document_id,
                    "page_number": call.get("page_number"),
                    "chunk_id": call.get("chunk_id"),
                    "window": call.get("window", 1),
                    "collection_id": collection_id,
                }
                data = mcp.get_document_context(
                    document_id=document_id,
                    page_number=call.get("page_number"),
                    chunk_id=call.get("chunk_id"),
                    window=int(call.get("window", 1)),
                    max_chars=int(call.get("max_chars", 12000)),
                    collection_id=collection_id,
                )

            elif tool == "list_tables":
                args_for_log = {"collection_id": collection_id}
                data = mcp.list_tables(collection_id=collection_id)

            elif tool == "get_table_schema":
                identifier = call.get("identifier") or call.get("table") or call.get("filename")
                if not identifier:
                    raise ValueError("get_table_schema requires 'identifier'")
                args_for_log = {"identifier": identifier, "collection_id": collection_id}
                data = mcp.get_table_schema(identifier=identifier, collection_id=collection_id)

            elif tool == "get_table_rows":
                identifier = call.get("identifier") or call.get("table") or call.get("filename")
                if not identifier:
                    raise ValueError("get_table_rows requires 'identifier'")
                limit = int(call.get("limit", 200))
                args_for_log = {"identifier": identifier, "limit": limit, "collection_id": collection_id}
                data = mcp.get_table_rows(
                    identifier=identifier,
                    limit=limit,
                    collection_id=collection_id,
                )

            elif tool == "query_table":
                sql = call.get("sql") or call.get("query")
                if not sql:
                    raise ValueError("query_table requires 'sql'")
                max_rows = min(int(call.get("max_rows", _MAX_STRUCTURED_ROWS_PER_TOOL)), 2000)
                args_for_log = {"sql": sql, "max_rows": max_rows, "collection_id": collection_id}
                data = mcp.query_table(
                    sql=sql,
                    max_rows=max_rows,
                    collection_id=collection_id,
                )

            elif tool == "aggregate_table":
                identifier = call.get("identifier") or call.get("table") or call.get("filename")
                aggregate_col = call.get("aggregate_col") or call.get("column")
                agg_fn = call.get("agg_fn") or call.get("fn")
                if not identifier or not aggregate_col or not agg_fn:
                    raise ValueError("aggregate_table requires 'identifier', 'aggregate_col', 'agg_fn'")
                args_for_log = {
                    "identifier": identifier,
                    "aggregate_col": aggregate_col,
                    "agg_fn": agg_fn,
                    "group_by": call.get("group_by"),
                    "sort_by": call.get("sort_by"),
                    "limit": call.get("limit"),
                    "collection_id": collection_id,
                }
                data = mcp.aggregate_table(
                    identifier=identifier,
                    aggregate_col=aggregate_col,
                    agg_fn=agg_fn,
                    group_by=call.get("group_by"),
                    sort_by=call.get("sort_by"),
                    limit=int(call["limit"]) if call.get("limit") is not None else None,
                    collection_id=collection_id,
                )

            elif tool == "compute_portfolio_metric":
                identifier = call.get("identifier") or call.get("table") or call.get("filename")
                metric = call.get("metric")
                if not identifier or not metric:
                    raise ValueError("compute_portfolio_metric requires 'identifier' and 'metric'")
                limit = int(call.get("limit", 10))
                args_for_log = {"identifier": identifier, "metric": metric, "limit": limit, "collection_id": collection_id}
                data = mcp.compute_portfolio_metric(
                    identifier=identifier,
                    metric=metric,
                    limit=limit,
                    group_by_symbol=bool(call.get("group_by_symbol", True)),
                    collection_id=collection_id,
                )

            elif tool == "list_collections":
                args_for_log = {}
                data = mcp.list_collections()

            elif tool == "get_collection_info":
                args_for_log = {"collection_id": collection_id}
                data = mcp.get_collection_info(collection_id=collection_id)

            elif tool == "get_price_history":
                symbol = call.get("symbol") or call.get("ticker")
                if not symbol:
                    raise ValueError("get_price_history requires 'symbol'")
                args_for_log = {
                    "symbol": symbol,
                    "period": call.get("period", "1y"),
                    "interval": call.get("interval", "1d"),
                }
                data = mcp.get_price_history(
                    symbol=symbol,
                    period=call.get("period", "1y"),
                    interval=call.get("interval", "1d"),
                )

            elif tool == "get_security_classification":
                symbol = call.get("symbol") or call.get("ticker")
                if not symbol:
                    raise ValueError("get_security_classification requires 'symbol'")
                args_for_log = {"symbol": symbol}
                data = mcp.get_security_classification(symbol=symbol)

            elif tool == "get_company_profile":
                symbol = call.get("symbol") or call.get("ticker")
                if not symbol:
                    raise ValueError("get_company_profile requires 'symbol'")
                args_for_log = {"symbol": symbol}
                data = mcp.get_company_profile(symbol=symbol)

            elif tool == "get_company_news":
                symbol = call.get("symbol") or call.get("ticker")
                if not symbol:
                    raise ValueError("get_company_news requires 'symbol'")
                limit = int(call.get("limit", 10))
                args_for_log = {"symbol": symbol, "limit": limit}
                data = mcp.get_company_news(symbol=symbol, limit=limit)

            elif tool == "get_corporate_events":
                symbol = call.get("symbol") or call.get("ticker")
                if not symbol:
                    raise ValueError("get_corporate_events requires 'symbol'")
                types = call.get("types")
                since = call.get("since")
                limit = int(call.get("limit", 50))
                args_for_log = {
                    "symbol": symbol,
                    "since": since,
                    "types": types,
                    "limit": limit,
                }
                data = mcp.get_corporate_events(
                    symbol=symbol, since=since, types=types, limit=limit,
                )

            elif tool == "enrich_holdings":
                include = call.get("include")
                identifier = call.get("identifier")
                max_symbols = int(call.get("max_symbols", 100))
                args_for_log = {
                    "collection_id": collection_id,
                    "identifier": identifier,
                    "include": include,
                    "max_symbols": max_symbols,
                }
                data = mcp.enrich_holdings(
                    collection_id=collection_id,
                    identifier=identifier,
                    include=include,
                    max_symbols=max_symbols,
                )

            elif tool == "prep_for_meeting":
                when = call.get("when") or call.get("date")
                include_tlh = bool(call.get("include_tlh", True))
                include_market_context = bool(call.get("include_market_context", True))
                top_n = int(call.get("top_n", 10))
                args_for_log = {
                    "collection_id": collection_id,
                    "when": when,
                    "include_tlh": include_tlh,
                    "include_market_context": include_market_context,
                    "top_n": top_n,
                }
                data = mcp.prep_for_meeting(
                    collection_id=collection_id,
                    when=when,
                    household_collection_ids=call.get("household_collection_ids"),
                    include_tlh=include_tlh,
                    include_market_context=include_market_context,
                    top_n=top_n,
                )

            elif tool == "get_client_profile":
                args_for_log = {"collection_id": collection_id}
                data = mcp.get_client_profile(collection_id=collection_id)

            elif tool == "get_meeting_notes":
                args_for_log = {
                    "collection_id": collection_id,
                    "document_id": call.get("document_id"),
                    "since": call.get("since"),
                    "until": call.get("until"),
                }
                data = mcp.get_meeting_notes(
                    collection_id=collection_id,
                    document_id=call.get("document_id"),
                    since=call.get("since"),
                    until=call.get("until"),
                )

            elif tool == "list_action_items":
                # "all" is the schema's way of saying "no status filter"; the
                # MCP tool spells that as None.
                status = call.get("status", "open")
                if status == "all":
                    status = None
                args_for_log = {
                    "collection_id": collection_id,
                    "status": status,
                    "assignee": call.get("assignee"),
                }
                data = mcp.list_action_items(
                    collection_id=collection_id,
                    status=status,
                    assignee=call.get("assignee"),
                )

            elif tool == "find_tax_loss_candidates":
                min_loss = float(call.get("min_loss", 500.0))
                max_candidates = int(call.get("max_candidates", 25))
                min_loss_pct = call.get("min_loss_pct")
                args_for_log = {
                    "collection_id": collection_id,
                    "min_loss": min_loss,
                    "min_loss_pct": min_loss_pct,
                    "max_candidates": max_candidates,
                }
                data = mcp.find_tax_loss_candidates(
                    collection_id=collection_id,
                    household_collection_ids=call.get("household_collection_ids"),
                    min_loss=min_loss,
                    min_loss_pct=float(min_loss_pct) if min_loss_pct is not None else None,
                    max_candidates=max_candidates,
                )

            elif tool == "generate_meeting_brief":
                top_n = int(call.get("top_n", 10))
                args_for_log = {
                    "collection_id": collection_id,
                    "tax_loss_min": float(call.get("tax_loss_min", 500.0)),
                    "concentration_pct": float(call.get("concentration_pct", 10.0)),
                    "cash_drag_min": float(call.get("cash_drag_min", 50000.0)),
                    "top_n": top_n,
                }
                data = mcp.generate_meeting_brief(
                    collection_id=collection_id,
                    tax_loss_min=float(call.get("tax_loss_min", 500.0)),
                    concentration_pct=float(call.get("concentration_pct", 10.0)),
                    cash_drag_min=float(call.get("cash_drag_min", 50000.0)),
                    top_n=top_n,
                )

            else:
                raise ValueError(f"No dispatcher for tool '{tool}'")

            logger.info("[agent] tool=%s args=%s -> ok", tool, _summarize_args(args_for_log))
            results.append({"tool": tool, "args": args_for_log, "result": data})

        except SQLValidationError as e:
            logger.info("[agent] tool=%s args=%s -> sql_rejected: %s", tool, _summarize_args(args_for_log), e)
            results.append({"tool": tool, "args": args_for_log or call, "error": f"SQL rejected: {e}"})
        except ValueError as e:
            logger.info("[agent] tool=%s args=%s -> error: %s", tool, _summarize_args(args_for_log), e)
            results.append({"tool": tool, "args": args_for_log or call, "error": str(e)})
        except Exception as e:
            logger.exception("[agent] tool=%s args=%s -> exception", tool, _summarize_args(args_for_log))
            results.append({"tool": tool, "args": args_for_log or call, "error": f"Tool execution failed: {e}"})
    return results


def _summarize_args(args: Dict[str, Any]) -> str:
    """Compact one-line representation of tool args for logs."""
    if not args:
        return "{}"
    parts = []
    for k, v in args.items():
        if v is None:
            continue
        if isinstance(v, str) and len(v) > 80:
            v = v[:77] + "..."
        parts.append(f"{k}={v!r}")
    return "{" + ", ".join(parts) + "}"


def format_results_for_prompt(results: List[Dict[str, Any]]) -> str:
    """Render tool results as JSON blocks for the next-pass prompt."""
    if not results:
        return ""
    lines = ["TOOL RESULTS:"]
    for r in results:
        payload = {k: v for k, v in r.items() if k in ("tool", "args", "result", "error")}
        # Truncate very large query results to keep the prompt bounded.
        result_obj = payload.get("result")
        if isinstance(result_obj, dict) and "rows" in result_obj:
            rows = result_obj.get("rows") or []
            if len(rows) > 50:
                payload["result"] = {**result_obj, "rows": rows[:50],
                                      "display_note": f"truncated to 50 of {len(rows)} rows for prompt"}
        lines.append("<tool_result>")
        lines.append(json.dumps(payload, default=str))
        lines.append("</tool_result>")
    return "\n".join(lines)


def strip_tool_calls(text: str) -> str:
    """Remove `<tool_call>` blocks from the final assistant text so users don't see them."""
    return _TOOL_CALL_RE.sub('', text).strip()


_DEFAULT_INLINE_ROW_LIMIT = 150
_DEFAULT_INLINE_CHAR_BUDGET = 60000
_CELL_CHAR_LIMIT = 240


def render_table_as_jsonl(
    store: HoldingsStore,
    table_info: Dict[str, Any],
    max_rows: int = _DEFAULT_INLINE_ROW_LIMIT,
) -> Optional[str]:
    """Dump a full structured table as JSONL (one row per line) using original column names.

    Returns None if the table is empty or the query fails. Long string values are
    truncated to keep prompt size bounded.
    """
    rendered = render_table_as_rows(store, table_info, max_rows=max_rows)
    if not rendered:
        return None
    columns = rendered["columns"]
    rows = rendered["rows"]
    lines: List[str] = []
    for row in rows:
        obj: Dict[str, Any] = {display_name: row[i] for i, display_name in enumerate(columns)}
        lines.append(json.dumps(obj, default=str, ensure_ascii=False))
    return "\n".join(lines)


def render_table_as_rows(
    store: HoldingsStore,
    table_info: Dict[str, Any],
    max_rows: int = _DEFAULT_INLINE_ROW_LIMIT,
) -> Optional[Dict[str, Any]]:
    """Return a full structured table as {"columns": [...], "rows": [[...], ...]}.

    Uses original (display) column names. Long string values are truncated
    to keep response size bounded. Returns None if the table is empty or the
    query fails.
    """
    table_name = table_info.get("table_name")
    if not table_name:
        return None
    if (table_info.get("row_count") or 0) == 0:
        return None

    sql = f'SELECT * FROM "{table_name}" ORDER BY __row_number'
    try:
        data = store.execute_query(sql, max_rows=max_rows)
    except Exception as e:
        logger.warning(f"render_table_as_rows failed for {table_name}: {e}")
        return None

    raw_columns: List[str] = data.get("columns") or []
    raw_rows: List[List[Any]] = data.get("rows") or []
    if not raw_columns or not raw_rows:
        return None

    sql_to_orig = {
        c["sql_name"]: c["name"]
        for c in table_info.get("columns", [])
        if c.get("sql_name") and c.get("name")
    }
    keep_idx: List[int] = [
        i for i, col in enumerate(raw_columns) if col != "__row_number"
    ]
    columns: List[str] = [
        sql_to_orig.get(raw_columns[i], raw_columns[i]) for i in keep_idx
    ]

    rows: List[List[Any]] = []
    for raw_row in raw_rows:
        projected: List[Any] = []
        for i in keep_idx:
            val = raw_row[i]
            if isinstance(val, str) and len(val) > _CELL_CHAR_LIMIT:
                val = val[:_CELL_CHAR_LIMIT] + "..."
            projected.append(val)
        rows.append(projected)

    return {"columns": columns, "rows": rows}


# Roles whose totals are money an advisor reads out loud. A table carrying
# any of these must not have its aggregates computed by the model in its head
# -- see ``_AGGREGATE_LOCK_RULE``.
_MONEY_ROLES = frozenset({"market_value", "cost_basis", "pnl"})


def table_is_money_bearing(table: Dict[str, Any]) -> bool:
    """True when a table has a column whose SUM is a figure someone acts on."""
    for col in table.get("columns", []) or []:
        if col.get("role") in _MONEY_ROLES:
            return True
    return False


def build_structured_context(
    tables: List[Dict[str, Any]],
    stores: Dict[str, HoldingsStore],
    inline_row_threshold: int = _DEFAULT_INLINE_ROW_LIMIT,
    total_char_budget: int = _DEFAULT_INLINE_CHAR_BUDGET,
) -> Dict[str, Any]:
    """Split structured tables into (small → inline JSONL, large → tool loop).

    For small tables we render the FULL contents into the prompt so the model
    can answer numeric/aggregation questions without depending on top-K chunk
    retrieval. For large tables we fall back to the SQL tool-use loop.

    **Money-bearing tables are an exception to "just do the arithmetic".**
    Inlining plus "show your arithmetic" asks the model to hand-sum a column,
    and a model that hand-sums a column gets it wrong. Measured 2026-08-17 on
    a 14-row fixture, three identical turns: $186,325.45, $184,211.45, and a
    correct refusal, against a true total of $207,727.45. Each wrong answer
    listed the right nine addends and showed its working underneath, which
    makes a wrong total *more* persuasive, not less.

    Worse, that path bypasses the P0.6 guard entirely — the guard lives in
    ``compute_portfolio_metric`` / ``generate_meeting_brief``, and a model
    doing mental arithmetic calls neither. Since the inline threshold is 150
    rows, essentially every real client export took the unguarded path.

    So these tables are still inlined (row-level lookup is genuinely useful
    and exact), but they are also reported in ``money_tables`` so the caller
    can advertise the structured tools for them and forbid hand-computed
    aggregates. Exactness comes from the tool; the rows are for context.

    Returns a dict:
      - inline_block: str, full JSONL prompt section (or '')
      - tool_tables: list[dict], the subset still needing the tool loop
      - money_tables: list[dict], inlined tables whose aggregates must go
        through the tools rather than being summed in the prompt
      - inlined_filenames: set[str], filenames fully represented in the prompt
      - inlined_document_ids: set[str], document_ids fully represented
    """
    inline_parts: List[str] = []
    tool_tables: List[Dict[str, Any]] = []
    money_tables: List[Dict[str, Any]] = []
    inlined_filenames: Set[str] = set()
    inlined_document_ids: Set[str] = set()
    running = 0

    for t in tables:
        row_count = t.get("row_count") or 0
        cid = t.get("collection_id")
        store = stores.get(cid) if cid else None

        if not store or row_count == 0 or row_count > inline_row_threshold:
            tool_tables.append(t)
            continue

        jsonl = render_table_as_jsonl(store, t, max_rows=inline_row_threshold)
        if jsonl is None:
            tool_tables.append(t)
            continue

        sheet_suffix = f" [sheet: {t['sheet_name']}]" if t.get("sheet_name") else ""
        role_cols = [
            f'{c["name"]}→{c["role"]}'
            for c in t.get("columns", [])
            if c.get("role")
        ]
        roles_line = f"  detected roles: {', '.join(role_cols)}\n" if role_cols else ""
        is_money = table_is_money_bearing(t)
        money_line = (
            "  AGGREGATE LOCK: totals/sums/averages for this file must come "
            "from the structured tools, not from adding these rows up.\n"
            if is_money else ""
        )
        header = (
            f'--- TABLE: {t["filename"]}{sheet_suffix} '
            f'({row_count} rows, {t.get("column_count", 0)} cols) ---\n'
            f'{roles_line}{money_line}'
        )
        block = header + jsonl

        if running + len(block) > total_char_budget:
            tool_tables.append(t)
            continue

        inline_parts.append(block)
        running += len(block)
        if is_money:
            money_tables.append(t)
        if t.get("filename"):
            inlined_filenames.add(t["filename"])
        if t.get("document_id"):
            inlined_document_ids.add(t["document_id"])

    inline_block = ""
    if inline_parts:
        lock_note = ""
        if money_tables:
            names = ", ".join(sorted({t["filename"] for t in money_tables}))
            lock_note = (
                "\nAGGREGATE LOCK — applies to: " + names + "\n"
                "These files hold money columns. Do NOT add, average, or rank "
                "their monetary values yourself, and do NOT present a total you "
                "worked out from the rows above. Call `compute_portfolio_metric` "
                "(or `query_table` / `aggregate_table` for anything it does not "
                "cover) and report what it returns. Two reasons, both of which "
                "have bitten: hand arithmetic over a column of figures is "
                "unreliable even when every row is visible, and the tools carry "
                "coercion guards that catch a column silently ingested as text — "
                "a check you cannot perform by reading. If a tool withholds a "
                "value (`value: null`, `reliable: false`), say the figure could "
                "not be computed and give its reason. Never substitute your own "
                "sum for a withheld one.\n"
            )
        inline_block = (
            "STRUCTURED TABLES (AUTHORITATIVE FULL DATA):\n"
            "The complete contents of the following CSV/XLSX files are included "
            "below as JSONL — one JSON object per row, every row present. Use "
            "them for row-level lookup, filtering, counting and date ranges, and "
            "prefer them over chunk snippets, which are partial. For monetary "
            "aggregates see the AGGREGATE LOCK below.\n"
            + lock_note
            + "\n"
            + "\n\n".join(inline_parts)
        )

    return {
        "inline_block": inline_block,
        "tool_tables": tool_tables,
        "money_tables": money_tables,
        "inlined_filenames": inlined_filenames,
        "inlined_document_ids": inlined_document_ids,
    }


def collect_structured_tables(collection_ids: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, HoldingsStore]]:
    """Gather structured tables from every collection in scope.

    Returns (tables, stores_by_collection).
    """
    from services.indexer_manager import indexer_manager

    all_tables: List[Dict[str, Any]] = []
    stores: Dict[str, HoldingsStore] = {}
    for cid in collection_ids:
        try:
            indexer = indexer_manager.get_indexer(cid)
        except Exception as e:
            logger.warning(f"collect_structured_tables: cannot load indexer '{cid}': {e}")
            continue
        store = indexer.vector_store.holdings_store
        stores[cid] = store
        try:
            tables = store.list_tables()
        except Exception as e:
            logger.warning(f"collect_structured_tables: list_tables failed for '{cid}': {e}")
            continue
        for t in tables:
            t_copy = dict(t)
            t_copy["collection_id"] = cid
            all_tables.append(t_copy)
    return all_tables, stores
