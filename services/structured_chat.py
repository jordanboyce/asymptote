"""Helpers for wiring structured CSV/XLSX query tools into the chat loop.

The chat endpoint uses a provider-agnostic ReAct-style tool-use loop: we
inject table schemas + tool-use instructions into the prompt, parse
`<tool_call>{...}</tool_call>` blocks out of the model's response, execute
them against the per-collection StructuredStore, and feed the results back
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
from services.structured_store import SQLValidationError, StructuredStore

logger = logging.getLogger(__name__)


_TOOL_CALL_RE = re.compile(
    r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
    re.DOTALL,
)

_MAX_STRUCTURED_ROWS_PER_TOOL = 200
_MAX_SCHEMA_PROMPT_COLS = 40

SUPPORTED_TOOLS = {"query_table", "compute_portfolio_metric"}


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
    """Render the tool-use protocol description for the system prompt."""
    return (
        "TOOL USE PROTOCOL:\n"
        "Whenever the user asks a numeric, aggregation, filtering, ranking, "
        "sum, average, top-N, bottom-N, breakdown, concentration, or exposure "
        "question about the tables above, you MUST call one or more tools "
        "BEFORE answering. Do not guess or estimate numbers from memory.\n\n"
        "To call a tool, emit a JSON block wrapped in <tool_call>...</tool_call> "
        "on its own line. You may call multiple tools in one turn by emitting "
        "multiple blocks. After the tool results come back you will be asked "
        "to produce a final answer.\n\n"
        "Available tools:\n"
        "  1. query_table — run read-only SQL SELECT against the tables.\n"
        '     <tool_call>{"tool": "query_table", "sql": "SELECT \\"sector\\", SUM(\\"market_value\\") FROM \\"csv_data_abc\\" GROUP BY \\"sector\\" ORDER BY 2 DESC"}</tool_call>\n'
        "     Rules: SELECT / WITH only; single statement; quote every identifier "
        "     in double quotes; column names are case-sensitive; use the exact "
        "     sql_name shown in the schema.\n\n"
        "  2. compute_portfolio_metric — run a canned financial portfolio metric.\n"
        "     Only use this when the table has financial roles (market_value, pnl, etc.).\n"
        '     <tool_call>{"tool": "compute_portfolio_metric", "table": "csv_data_abc", "metric": "top_holdings", "limit": 5}</tool_call>\n'
        "     Available metrics: row_count, total_market_value, total_cost_basis, "
        "total_pnl, top_holdings, bottom_holdings, largest_gains, largest_losses, "
        "concentration, breakdown_by_sector, breakdown_by_asset_class, "
        "breakdown_by_region, breakdown_by_currency, weighted_return, "
        "summary_statistics. `table` accepts a table_name, filename, or document_id.\n\n"
        "If the answer doesn't need structured data (narrative questions, "
        "summaries of prose documents) you can answer directly without tool calls. "
        "When you do answer after tool calls, cite the numbers verbatim from the "
        "tool results and NEVER invent a value the tools didn't return."
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
    stores: Dict[str, StructuredStore],
    tables: List[Dict[str, Any]],
) -> Optional[StructuredStore]:
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
    stores: Dict[str, StructuredStore],
    tables: List[Dict[str, Any]],
) -> Optional[StructuredStore]:
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


def execute_tool_calls(
    calls: List[Dict[str, Any]],
    stores: Dict[str, StructuredStore],
    tables: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Run each parsed tool call and return a parallel list of result dicts.

    `stores` maps collection_id → StructuredStore. `tables` is the combined
    table list from `collect_structured_tables`, with a `collection_id` field
    on every entry. When the same table exists in multiple collections the
    first match wins.
    """
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
        # Accept legacy name emitted by older prompts
        if tool == "query_structured_table":
            tool = "query_table"
        if tool not in SUPPORTED_TOOLS:
            results.append({
                "tool": tool,
                "args": call,
                "error": f"Unknown tool '{tool}'. Supported: {sorted(SUPPORTED_TOOLS)}",
            })
            continue

        try:
            if tool == "query_table":
                sql = call.get("sql") or call.get("query")
                if not sql:
                    raise ValueError("query_table requires 'sql'")
                store = _resolve_store_for_sql(sql, stores, tables)
                if store is None:
                    raise ValueError("No structured tables available in this collection.")
                max_rows = min(
                    int(call.get("max_rows", _MAX_STRUCTURED_ROWS_PER_TOOL)),
                    _MAX_STRUCTURED_ROWS_PER_TOOL,
                )
                data = store.execute_query(sql, max_rows=max_rows)
                results.append({
                    "tool": "query_table",
                    "args": {"sql": sql, "max_rows": max_rows},
                    "result": data,
                })
            elif tool == "compute_portfolio_metric":
                identifier = call.get("table") or call.get("identifier") or call.get("filename")
                metric = call.get("metric")
                if not identifier or not metric:
                    raise ValueError("compute_portfolio_metric requires 'table' and 'metric'")
                store = _resolve_store_for_identifier(identifier, stores, tables)
                if store is None:
                    raise ValueError("No structured tables available in this collection.")
                limit = int(call.get("limit", 10))
                data = compute_financial_metric(store, identifier, metric, limit=limit)
                results.append({
                    "tool": tool,
                    "args": {"table": identifier, "metric": metric, "limit": limit},
                    "result": data,
                })
        except SQLValidationError as e:
            results.append({"tool": tool, "args": call, "error": f"SQL rejected: {e}"})
        except ValueError as e:
            results.append({"tool": tool, "args": call, "error": str(e)})
        except Exception as e:
            logger.exception(f"Structured tool call failed: {tool}")
            results.append({"tool": tool, "args": call, "error": f"Tool execution failed: {e}"})
    return results


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
    store: StructuredStore,
    table_info: Dict[str, Any],
    max_rows: int = _DEFAULT_INLINE_ROW_LIMIT,
) -> Optional[str]:
    """Dump a full structured table as JSONL (one row per line) using original column names.

    Returns None if the table is empty or the query fails. Long string values are
    truncated to keep prompt size bounded.
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
        logger.warning(f"render_table_as_jsonl failed for {table_name}: {e}")
        return None

    columns: List[str] = data.get("columns") or []
    rows: List[List[Any]] = data.get("rows") or []
    if not columns or not rows:
        return None

    sql_to_orig = {
        c["sql_name"]: c["name"]
        for c in table_info.get("columns", [])
        if c.get("sql_name") and c.get("name")
    }
    keep: List[Tuple[int, str]] = [
        (i, sql_to_orig.get(col, col))
        for i, col in enumerate(columns)
        if col != "__row_number"
    ]

    lines: List[str] = []
    for row in rows:
        obj: Dict[str, Any] = {}
        for idx, display_name in keep:
            val = row[idx]
            if isinstance(val, str) and len(val) > _CELL_CHAR_LIMIT:
                val = val[:_CELL_CHAR_LIMIT] + "..."
            obj[display_name] = val
        lines.append(json.dumps(obj, default=str, ensure_ascii=False))
    return "\n".join(lines)


def build_structured_context(
    tables: List[Dict[str, Any]],
    stores: Dict[str, StructuredStore],
    inline_row_threshold: int = _DEFAULT_INLINE_ROW_LIMIT,
    total_char_budget: int = _DEFAULT_INLINE_CHAR_BUDGET,
) -> Dict[str, Any]:
    """Split structured tables into (small → inline JSONL, large → tool loop).

    For small tables we render the FULL contents into the prompt so the model
    can answer numeric/aggregation questions without depending on top-K chunk
    retrieval. For large tables we fall back to the SQL tool-use loop.

    Returns a dict:
      - inline_block: str, full JSONL prompt section (or '')
      - tool_tables: list[dict], the subset still needing the tool loop
      - inlined_filenames: set[str], filenames fully represented in the prompt
      - inlined_document_ids: set[str], document_ids fully represented
    """
    inline_parts: List[str] = []
    tool_tables: List[Dict[str, Any]] = []
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
        header = (
            f'--- TABLE: {t["filename"]}{sheet_suffix} '
            f'({row_count} rows, {t.get("column_count", 0)} cols) ---\n'
            f'{roles_line}'
        )
        block = header + jsonl

        if running + len(block) > total_char_budget:
            tool_tables.append(t)
            continue

        inline_parts.append(block)
        running += len(block)
        if t.get("filename"):
            inlined_filenames.add(t["filename"])
        if t.get("document_id"):
            inlined_document_ids.add(t["document_id"])

    inline_block = ""
    if inline_parts:
        inline_block = (
            "STRUCTURED TABLES (AUTHORITATIVE FULL DATA):\n"
            "The complete contents of the following CSV/XLSX files are included "
            "below as JSONL — one JSON object per row, every row present. For "
            "any numeric, aggregation, sum, count, average, filter, ranking, or "
            "date-range question about these files, answer DIRECTLY and "
            "EXCLUSIVELY from the JSONL rows below. Do NOT rely on any chunk "
            "snippets for these files — the JSONL is complete and authoritative. "
            "Show your arithmetic when summing so the user can verify.\n\n"
            + "\n\n".join(inline_parts)
        )

    return {
        "inline_block": inline_block,
        "tool_tables": tool_tables,
        "inlined_filenames": inlined_filenames,
        "inlined_document_ids": inlined_document_ids,
    }


def collect_structured_tables(collection_ids: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, StructuredStore]]:
    """Gather structured tables from every collection in scope.

    Returns (tables, stores_by_collection).
    """
    from services.indexer_manager import indexer_manager

    all_tables: List[Dict[str, Any]] = []
    stores: Dict[str, StructuredStore] = {}
    for cid in collection_ids:
        try:
            indexer = indexer_manager.get_indexer(cid)
        except Exception as e:
            logger.warning(f"collect_structured_tables: cannot load indexer '{cid}': {e}")
            continue
        store = indexer.vector_store.structured_store
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
