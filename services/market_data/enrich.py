"""Composite enrichment over a holdings table.

Walks a holdings-style table in a collection, pulls the distinct ticker
symbols out of the column tagged with the `ticker` role, and calls the
individual enrichment feeds (classification, company profile, optional
corporate events and price change) for each one. Returns a joined view
that lets an LLM answer questions like sector breakdown, growth-vs-value,
"any CEO changes?", or "what's in a downtrend?" in a single tool call.

Every underlying feed caches on disk, so a re-run against the same
collection within the TTL window returns near-instantly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

_MAX_SYMBOLS_DEFAULT = 100


def _distinct_symbols(store: Any, schema: dict[str, Any]) -> list[str]:
    """Pull distinct non-empty ticker values from the table's symbol column."""
    cols = schema.get("columns") or []
    ticker_col = next(
        (c for c in cols if c.get("role") == "ticker"),
        None,
    )
    if ticker_col is None:
        return []

    table_name = schema["table_name"]
    sql_name = ticker_col["sql_name"]
    sql = (
        f'SELECT DISTINCT TRIM("{sql_name}") AS sym '
        f'FROM "{table_name}" '
        f'WHERE TRIM(COALESCE("{sql_name}", \'\')) != \'\' '
        f'ORDER BY sym'
    )
    try:
        result = store.execute_query(sql, max_rows=5000)
    except Exception as exc:
        logger.warning(f"distinct-symbol query failed for {table_name}: {exc}")
        return []

    symbols: list[str] = []
    for row in result.get("rows", []):
        if not row:
            continue
        val = row[0]
        if val is None:
            continue
        s = str(val).strip().upper()
        if s and s not in symbols:
            symbols.append(s)
    return symbols


def _find_holdings_schema(store: Any, identifier: str | None) -> dict[str, Any] | None:
    """Pick the right table: explicit identifier if given, else first table with a ticker role."""
    if identifier:
        return store.get_schema(identifier)

    for t in store.list_tables():
        roles = set((t.get("financial_roles") or {}).values())
        if "ticker" in roles:
            return store.get_schema(t["table_name"])
    return None


def enrich_holdings(
    collection_id: str,
    identifier: str | None = None,
    include: Iterable[str] | None = None,
    max_symbols: int = _MAX_SYMBOLS_DEFAULT,
    *,
    # Injectable for testing — defaults to the real feeds.
    store_factory: Callable[[str], Any] | None = None,
    classification_fn: Callable[[str], dict[str, Any]] | None = None,
    profile_fn: Callable[[str], dict[str, Any]] | None = None,
    events_fn: Callable[..., dict[str, Any]] | None = None,
    price_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Enrich every distinct ticker in a collection's holdings table.

    `include` controls which feeds to call per symbol (default: classification
    + profile). Valid values: "classification", "profile", "events", "price_1y".
    """
    include_set = {s.strip().lower() for s in (include or ["classification", "profile"]) if s and s.strip()}
    valid = {"classification", "profile", "events", "price_1y"}
    unknown = include_set - valid
    if unknown:
        return {
            "error": "invalid_include",
            "message": f"Unknown include option(s): {sorted(unknown)}. Valid: {sorted(valid)}",
        }

    if store_factory is None:
        from services.mcp_server import _get_structured_store as _default_factory
        store_factory = _default_factory
    if classification_fn is None:
        from services.market_data.classification import get_security_classification as _cls
        classification_fn = _cls
    if profile_fn is None:
        from services.market_data.company import get_company_profile as _prof
        profile_fn = _prof
    if events_fn is None:
        from services.market_data.corporate_events import get_corporate_events as _ev
        events_fn = _ev
    if price_fn is None:
        from services.market_data.price_history import get_price_history as _px
        price_fn = _px

    store = store_factory(collection_id)
    schema = _find_holdings_schema(store, identifier)
    if schema is None:
        return {
            "error": "no_holdings_table",
            "message": (
                f"No table with a detected `ticker` role found in collection "
                f"'{collection_id}'. Call list_tables + get_table_schema to "
                f"confirm the collection has a holdings file ingested."
            ),
            "collection_id": collection_id,
        }

    symbols = _distinct_symbols(store, schema)
    if not symbols:
        return {
            "error": "no_symbols",
            "message": f"No ticker values found in column for table {schema.get('table_name')}.",
            "collection_id": collection_id,
            "table_name": schema.get("table_name"),
        }

    capped = max(1, min(int(max_symbols), 500))
    truncated = len(symbols) > capped
    symbols = symbols[:capped]

    enriched: list[dict[str, Any]] = []
    one_year_ago = (datetime.now(tz=timezone.utc).date() - timedelta(days=365)).isoformat()

    for sym in symbols:
        entry: dict[str, Any] = {"symbol": sym}
        if "classification" in include_set:
            entry["classification"] = classification_fn(sym)
        if "profile" in include_set:
            entry["profile"] = profile_fn(sym)
        if "events" in include_set:
            entry["events"] = events_fn(sym, since=one_year_ago)
        if "price_1y" in include_set:
            entry["price_1y"] = price_fn(sym, start=one_year_ago)
        enriched.append(entry)

    return {
        "collection_id": collection_id,
        "table_name": schema.get("table_name"),
        "include": sorted(include_set),
        "symbol_count": len(enriched),
        "truncated": truncated,
        "holdings": enriched,
        "source": "enrich_holdings",
    }
