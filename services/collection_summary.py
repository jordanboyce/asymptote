"""Per-Collection portfolio snapshot for the Collection card UI.

Given a HoldingsStore, returns a small dict — Position count, distinct
Account count, most-recent-export timestamp, and source file list — that
the Collection detail view renders before opening the document table.

Position counting prefers the ``<table>__by_symbol`` rollup view (Lots
collapsed to Positions) when the table has a symbol/cusip/isin role and
the view exists. Otherwise it falls back to raw ``row_count`` so flat
tables without a security identifier still show a meaningful number.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Set


def compute_collection_summary(store: Any, collection_id: str) -> Dict[str, Any]:
    tables = store.list_tables()
    positions = 0
    accounts: Set[str] = set()
    source_files: List[Dict[str, Any]] = []
    most_recent_export_iso: Optional[str] = None

    uri = f"file:{store.db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        created_at_by_table: Dict[str, str] = {
            row[0]: row[1]
            for row in conn.execute(
                f"SELECT table_name, created_at FROM {store.SCHEMA_REGISTRY}"
            )
        }

        for tbl in tables:
            base_table = tbl["table_name"]
            roles_map = tbl.get("financial_roles") or {}
            symbol_col = next(
                (sql_name for sql_name, role in roles_map.items()
                 if role in ("ticker", "cusip", "isin")),
                None,
            )
            account_col = next(
                (sql_name for sql_name, role in roles_map.items() if role == "account"),
                None,
            )

            view_name = f"{base_table}__by_symbol"
            view_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
                (view_name,),
            ).fetchone()
            if view_exists and symbol_col:
                row = conn.execute(f'SELECT COUNT(*) FROM "{view_name}"').fetchone()
                positions += int(row[0] or 0)
            else:
                positions += int(tbl.get("row_count") or 0)

            if account_col:
                for row in conn.execute(
                    f'SELECT DISTINCT "{account_col}" FROM "{base_table}" '
                    f'WHERE "{account_col}" IS NOT NULL '
                    f'AND TRIM(CAST("{account_col}" AS TEXT)) != \'\''
                ):
                    accounts.add(str(row[0]).strip())

            created_at = created_at_by_table.get(base_table)
            if created_at and (
                most_recent_export_iso is None or created_at > most_recent_export_iso
            ):
                most_recent_export_iso = created_at

            source_files.append({
                "filename": tbl.get("filename"),
                "document_id": tbl.get("document_id"),
                "row_count": tbl.get("row_count"),
                "created_at": created_at,
            })

    return {
        "collection_id": collection_id,
        "positions": positions,
        "accounts": len(accounts),
        "most_recent_export_iso": most_recent_export_iso,
        "source_files": source_files,
    }
