"""Per-Collection store for ingested brokerage Holdings tables.

Each ingested CSV/XLSX becomes a typed SQLite table inside the Collection's
``metadata.db``; the ``csv_schemas`` registry table maps documents to their
physical tables and caches inferred schema and column roles. When a table
contains a ticker/cusip/isin column, an auxiliary ``<table>__by_symbol``
view rolls per-Lot rows up to per-Position rows.

This Module owns brokerage-domain knowledge: it detects financial column
roles (via :mod:`services.financial.roles`), recognises currency- and
percent-formatted cells (via :mod:`services.financial.type_hints`), and
knows lot-vs-rollup signals like Pershing's ``original_quantity``. Generic
type inference and SQL validation live in :mod:`services.tabular`.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.financial.roles import detect_financial_role
from services.financial.type_hints import FINANCIAL_TYPE_EXTENSIONS
from services.tabular.inference import (
    CORE_TYPE_TO_SQLITE,
    coerce_value,
    infer_column_type,
    raw_storage_value,
    sanitize_identifier,
    should_preserve_raw,
)
from services.tabular.sql_validation import SQLValidationError, validate_select

logger = logging.getLogger(__name__)


_DEFAULT_MAX_QUERY_ROWS = 1000


class HoldingsStore:
    """Manages per-document typed SQL tables and their schema registry.

    Tables are stored inside the Collection's metadata SQLite database using
    ``csv_data_{document_id}[_sheet]`` names. The ``csv_schemas`` registry
    table maps documents to their physical tables and caches inferred
    schema/roles.
    """

    META_TABLE_PREFIX = 'csv_data_'
    SCHEMA_REGISTRY = 'csv_schemas'

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f'''
                CREATE TABLE IF NOT EXISTS {self.SCHEMA_REGISTRY} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    sheet_name TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL,
                    table_name TEXT NOT NULL UNIQUE,
                    row_count INTEGER NOT NULL,
                    column_count INTEGER NOT NULL,
                    schema_json TEXT NOT NULL,
                    financial_roles TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(document_id, sheet_name)
                )
            ''')
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS idx_csv_schemas_doc '
                f'ON {self.SCHEMA_REGISTRY}(document_id)'
            )
            conn.commit()

    def _table_name(self, document_id: str, sheet_name: str = '') -> str:
        safe_doc = sanitize_identifier(document_id, fallback='doc')
        if sheet_name:
            safe_sheet = sanitize_identifier(sheet_name, fallback='sheet')
            return f'{self.META_TABLE_PREFIX}{safe_doc}_{safe_sheet}'
        return f'{self.META_TABLE_PREFIX}{safe_doc}'

    def create_table(
        self,
        document_id: str,
        filename: str,
        columns: List[str],
        rows: List[Dict[str, Any]],
        sheet_name: str = '',
        role_overrides: Optional[Dict[str, str]] = None,
        type_overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create a typed table + schema entry for an ingested tabular file.

        Parameters
        ----------
        role_overrides:
            Optional mapping of original column name → semantic role string.
            Overrides the heuristic role detector result. Supplied by the
            Vendor Profile system.
        type_overrides:
            Optional mapping of original column name → type string
            (``'real'``, ``'currency'``, ``'percent'``, ``'date'``, etc.).
            Overrides the heuristic ``infer_column_type`` result. Supplied
            by the Vendor Profile system.
        """
        if not columns:
            raise ValueError('Cannot create structured table with no columns')

        role_overrides = role_overrides or {}
        type_overrides = type_overrides or {}

        col_values: Dict[str, List[Any]] = {col: [row.get(col) for row in rows] for col in columns}

        col_infos: List[Dict[str, Any]] = []
        used_idents = set()
        for col in columns:
            info = infer_column_type(col_values[col], type_extensions=FINANCIAL_TYPE_EXTENSIONS)
            if col in type_overrides:
                info = dict(info, type=type_overrides[col])
            base_ident = sanitize_identifier(col, fallback='col')
            ident = base_ident
            counter = 1
            while ident.lower() in used_idents:
                counter += 1
                ident = f'{base_ident}_{counter}'
            used_idents.add(ident.lower())

            raw_sql_name: Optional[str] = None
            if should_preserve_raw(col_values[col], info['type']):
                raw_ident = f'{ident}__raw'
                raw_counter = 1
                while raw_ident.lower() in used_idents:
                    raw_counter += 1
                    raw_ident = f'{ident}__raw_{raw_counter}'
                used_idents.add(raw_ident.lower())
                raw_sql_name = raw_ident

            # Profile override beats heuristic; tag the source so callers can
            # decide how much to trust each role assignment.
            if col in role_overrides:
                role: Optional[str] = role_overrides[col]
                role_source: Optional[str] = 'profile'
            else:
                role = detect_financial_role(col, info['type'])
                role_source = 'heuristic' if role else None
            sample_values = [
                str(v)[:100]
                for v in col_values[col][:5]
                if v is not None and str(v).strip() != ''
            ]
            col_infos.append({
                'name': col,
                'sql_name': ident,
                'type': info['type'],
                'role': role,
                'role_source': role_source,
                'raw_sql_name': raw_sql_name,
                'null_count': info['null_count'],
                'distinct_count': info['distinct_count'],
                'stats': info['stats'],
                'samples': sample_values,
            })

        # LLM-assisted role inference for unmapped columns. Layering inversion:
        # this Module reaches up to config + the LLM service. To be revisited
        # when the Vendor Profile pipeline is reshaped (candidate #4).
        from config import settings as _cfg
        if _cfg.enable_llm_schema_inference:
            mapped = sum(1 for c in col_infos if c['role'])
            total = len(col_infos)
            unmapped_frac = 1.0 - (mapped / total) if total else 0.0
            if unmapped_frac >= _cfg.llm_schema_inference_threshold:
                unmapped_cols = [c for c in col_infos if not c['role']]
                logger.info(
                    "LLM schema inference triggered: %d/%d columns unmapped (%.0f%%)",
                    len(unmapped_cols), total, unmapped_frac * 100,
                )
                try:
                    from services.llm_role_inference import infer_roles_with_llm
                    llm_roles = infer_roles_with_llm(
                        unmapped_columns=unmapped_cols,
                        collection_id=None,
                    )
                    for c in col_infos:
                        if not c['role'] and c['name'] in llm_roles:
                            c['role'] = llm_roles[c['name']]
                            c['role_source'] = 'llm'
                except Exception as exc:
                    logger.warning("LLM schema inference failed, continuing without: %s", exc)

        table_name = self._table_name(document_id, sheet_name)

        type_to_sqlite = dict(CORE_TYPE_TO_SQLITE)
        for ext in FINANCIAL_TYPE_EXTENSIONS:
            type_to_sqlite[ext.name] = ext.sqlite_type

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            col_defs = ['__row_number INTEGER PRIMARY KEY']
            for info in col_infos:
                sqlite_type = type_to_sqlite.get(info['type'], 'TEXT')
                col_defs.append(f'"{info["sql_name"]}" {sqlite_type}')
                if info.get('raw_sql_name'):
                    col_defs.append(f'"{info["raw_sql_name"]}" TEXT')
            conn.execute(f'CREATE TABLE "{table_name}" ({", ".join(col_defs)})')

            insert_columns = ['__row_number']
            for c in col_infos:
                insert_columns.append(c['sql_name'])
                if c.get('raw_sql_name'):
                    insert_columns.append(c['raw_sql_name'])
            placeholders = ', '.join(['?'] * len(insert_columns))
            insert_cols_sql = '"' + '", "'.join(insert_columns) + '"'
            insert_sql = f'INSERT INTO "{table_name}" ({insert_cols_sql}) VALUES ({placeholders})'

            batch: List[List[Any]] = []
            for idx, row in enumerate(rows, start=1):
                values: List[Any] = [idx]
                for info in col_infos:
                    raw_value = row.get(info['name'])
                    values.append(coerce_value(raw_value, info['type'], type_extensions=FINANCIAL_TYPE_EXTENSIONS))
                    if info.get('raw_sql_name'):
                        values.append(raw_storage_value(raw_value))
                batch.append(values)
                if len(batch) >= 500:
                    conn.executemany(insert_sql, batch)
                    batch = []
            if batch:
                conn.executemany(insert_sql, batch)

            financial_roles = {c['sql_name']: c['role'] for c in col_infos if c['role']}
            schema_payload = {
                'document_id': document_id,
                'sheet_name': sheet_name,
                'filename': filename,
                'table_name': table_name,
                'row_count': len(rows),
                'column_count': len(col_infos),
                'columns': col_infos,
            }

            conn.execute(
                f'''INSERT INTO {self.SCHEMA_REGISTRY}
                    (document_id, sheet_name, filename, table_name, row_count,
                     column_count, schema_json, financial_roles)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(document_id, sheet_name) DO UPDATE SET
                        filename = excluded.filename,
                        table_name = excluded.table_name,
                        row_count = excluded.row_count,
                        column_count = excluded.column_count,
                        schema_json = excluded.schema_json,
                        financial_roles = excluded.financial_roles,
                        created_at = CURRENT_TIMESTAMP''',
                (
                    document_id, sheet_name, filename, table_name,
                    len(rows), len(col_infos),
                    json.dumps(schema_payload, default=str),
                    json.dumps(financial_roles),
                ),
            )
            conn.commit()

        logger.info(
            f"Created Holdings table {table_name}: {len(rows)} rows × {len(col_infos)} cols "
            f"(roles: {sorted(set(financial_roles.values())) or 'none'})"
        )

        # Auto-create a by-symbol rollup view when a symbol/ticker role is
        # detected. Brokerage files typically have one row per Lot; the view
        # aggregates Lots into Positions.
        symbol_col_info = next(
            (c for c in col_infos if c.get('role') in ('ticker', 'cusip', 'isin')),
            None,
        )
        if symbol_col_info:
            self._create_symbol_rollup_view(table_name, col_infos, symbol_col_info['sql_name'])

        return schema_payload

    def _create_symbol_rollup_view(
        self,
        table_name: str,
        col_infos: List[Dict[str, Any]],
        symbol_col: str,
    ) -> None:
        """Create a ``<table>__by_symbol`` VIEW aggregating Lot rows per Position.

        SUMs market_value, cost_basis, pnl, and quantity across Lots; COUNTs
        Lot rows; and computes a weighted-average unit cost when both
        cost_basis and quantity are present. Non-numeric / non-role columns
        are omitted to avoid ambiguous GROUP BY semantics.

        Excludes parent-rollup rows (Pershing-style files include both a
        Position-level summary row AND its child Lot rows for the same
        security; summing both double-counts) and footer/total rows.
        """
        SUMMABLE_ROLES = {'market_value', 'cost_basis', 'pnl', 'quantity'}
        FIRST_ROLES = {'name', 'asset_class', 'sector', 'region', 'currency', 'account', 'rating'}

        sql_name_by_norm: Dict[str, str] = {}
        for c in col_infos:
            orig = (c.get('name') or '').strip().lower()
            norm = re.sub(r'[\s_\-]+', '_', orig).strip('_')
            if norm:
                sql_name_by_norm[norm] = c['sql_name']
            sql_name_by_norm[c['sql_name'].lower()] = c['sql_name']

        # A column populated only on Lot rows (and blank on parent rollups)
        # is the cleanest signal that lot/rollup duplication exists. Pershing
        # uses "Original Quantity"; other custodians may use "Lot Quantity",
        # "Acquired Quantity", "Open Date", etc.
        LOT_ONLY_HINTS = (
            'original_quantity', 'original_total_cost', 'original_adjusted_cost',
            'lot_quantity', 'acquired_quantity', 'acquisition_date', 'open_date',
        )
        lot_marker_col: Optional[str] = next(
            (sql_name_by_norm[h] for h in LOT_ONLY_HINTS if h in sql_name_by_norm),
            None,
        )

        # Identifier columns we'll fall back to when symbol is blank, so
        # CUSIP-only securities (UITs, munis, CDs) each get their own bucket
        # instead of all collapsing into a single empty-symbol row.
        cusip_col = next(
            (c['sql_name'] for c in col_infos if c.get('role') == 'cusip' and c['sql_name'] != symbol_col),
            None,
        )
        secid_col = sql_name_by_norm.get('security_identifier') or sql_name_by_norm.get('security_id')
        name_col = next(
            (c['sql_name'] for c in col_infos if c.get('role') == 'name'),
            None,
        )

        group_key_parts = [f'NULLIF(TRIM("{symbol_col}"), \'\')']
        for fallback in (cusip_col, secid_col, name_col):
            if fallback and fallback != symbol_col:
                group_key_parts.append(f'NULLIF(TRIM(CAST("{fallback}" AS TEXT)), \'\')')
        group_key = f'COALESCE({", ".join(group_key_parts)})' if len(group_key_parts) > 1 else group_key_parts[0]

        select_parts: List[str] = [f'{group_key} AS "{symbol_col}"']
        cost_basis_col: Optional[str] = None
        qty_col: Optional[str] = None

        for c in col_infos:
            col = c['sql_name']
            role = c.get('role')
            if col == symbol_col:
                continue
            if role in SUMMABLE_ROLES:
                select_parts.append(f'SUM("{col}") AS "{col}"')
                if role == 'cost_basis':
                    cost_basis_col = col
                if role == 'quantity':
                    qty_col = col
            elif role in FIRST_ROLES:
                select_parts.append(f'MAX("{col}") AS "{col}"')

        select_parts.append('COUNT(*) AS lot_count')

        if cost_basis_col and qty_col:
            select_parts.append(
                f'CASE WHEN SUM("{qty_col}") != 0 '
                f'THEN SUM("{cost_basis_col}") / SUM("{qty_col}") '
                f'ELSE NULL END AS unit_cost'
            )

        # WHERE clause:
        #   - drop parent rollup rows (lot marker column is NULL/blank)
        #   - drop footer "TOTAL" rows (have no quantity)
        #   - drop rows where the grouping key is entirely NULL
        where_clauses: List[str] = [f'{group_key} IS NOT NULL']
        if lot_marker_col:
            where_clauses.append(
                f'("{lot_marker_col}" IS NOT NULL '
                f'AND TRIM(CAST("{lot_marker_col}" AS TEXT)) != \'\')'
            )
        if qty_col:
            where_clauses.append(f'"{qty_col}" IS NOT NULL')
        footer_marker_cols = [c for c in (name_col, secid_col, symbol_col) if c]
        for fc in footer_marker_cols:
            where_clauses.append(
                f'(UPPER(COALESCE(CAST("{fc}" AS TEXT), \'\')) NOT IN '
                f"('TOTAL', 'GRAND TOTAL', 'SUBTOTAL', 'TOTALS'))"
            )

        view_name = f'{table_name}__by_symbol'
        sql = (
            f'CREATE VIEW IF NOT EXISTS "{view_name}" AS\n'
            f'SELECT {", ".join(select_parts)}\n'
            f'FROM "{table_name}"\n'
            f'WHERE {" AND ".join(where_clauses)}\n'
            f'GROUP BY {group_key}'
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f'DROP VIEW IF EXISTS "{view_name}"')
            conn.execute(sql)
            conn.commit()
        logger.info(
            f"Created rollup view {view_name} "
            f"(group_key={group_key!r}, lot_marker={lot_marker_col!r})"
        )

    def delete_document(self, document_id: str) -> int:
        """Drop all Holdings tables + schema rows belonging to a document."""
        dropped = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f'SELECT table_name FROM {self.SCHEMA_REGISTRY} WHERE document_id = ?',
                (document_id,),
            )
            for (table_name,) in cursor.fetchall():
                conn.execute(f'DROP VIEW IF EXISTS "{table_name}__by_symbol"')
                conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                dropped += 1
            conn.execute(
                f'DELETE FROM {self.SCHEMA_REGISTRY} WHERE document_id = ?',
                (document_id,),
            )
            conn.commit()
        if dropped:
            logger.info(f'Dropped {dropped} Holdings table(s) for document {document_id}')
        return dropped

    def clear_all(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f'SELECT table_name FROM {self.SCHEMA_REGISTRY}')
            for (table_name,) in cursor.fetchall():
                conn.execute(f'DROP VIEW IF EXISTS "{table_name}__by_symbol"')
                conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            conn.execute(f'DELETE FROM {self.SCHEMA_REGISTRY}')
            conn.commit()

    def list_tables(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f'''SELECT document_id, sheet_name, filename, table_name,
                           row_count, column_count, schema_json, financial_roles
                    FROM {self.SCHEMA_REGISTRY}
                    ORDER BY filename, sheet_name'''
            )
            result = []
            for row in cursor.fetchall():
                schema = json.loads(row['schema_json'])
                roles = json.loads(row['financial_roles']) if row['financial_roles'] else {}
                result.append({
                    'document_id': row['document_id'],
                    'sheet_name': row['sheet_name'],
                    'filename': row['filename'],
                    'table_name': row['table_name'],
                    'row_count': row['row_count'],
                    'column_count': row['column_count'],
                    'columns': [
                        {
                            'name': c['name'],
                            'sql_name': c['sql_name'],
                            'type': c['type'],
                            'role': c.get('role'),
                            'role_source': c.get('role_source'),
                        }
                        for c in schema['columns']
                    ],
                    'financial_roles': roles,
                })
            return result

    def get_schema(
        self,
        identifier: str,
        identifier_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Look up a table schema by table_name, filename, or document_id.

        When identifier_type is provided ("table_name", "filename", or
        "document_id"), the lookup is restricted to that column — useful
        when multiple tables might match the same literal but you need a
        specific interpretation. When None (default), all three fields are
        searched.
        """
        if identifier_type is not None and identifier_type not in {
            "table_name", "filename", "document_id",
        }:
            raise ValueError(
                f"identifier_type must be one of 'table_name', 'filename', "
                f"'document_id', or None; got {identifier_type!r}"
            )

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if identifier_type is not None:
                cursor = conn.execute(
                    f'''SELECT schema_json FROM {self.SCHEMA_REGISTRY}
                        WHERE {identifier_type} = ?
                        ORDER BY sheet_name
                        LIMIT 1''',
                    (identifier,),
                )
            else:
                cursor = conn.execute(
                    f'''SELECT schema_json FROM {self.SCHEMA_REGISTRY}
                        WHERE table_name = ? OR filename = ? OR document_id = ?
                        ORDER BY sheet_name
                        LIMIT 1''',
                    (identifier, identifier, identifier),
                )
            row = cursor.fetchone()
            if not row:
                return None
            return json.loads(row['schema_json'])

    def get_schemas_for_document(self, document_id: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f'''SELECT schema_json FROM {self.SCHEMA_REGISTRY}
                    WHERE document_id = ? ORDER BY sheet_name''',
                (document_id,),
            )
            return [json.loads(r['schema_json']) for r in cursor.fetchall()]

    def execute_query(
        self,
        sql: str,
        max_rows: int = _DEFAULT_MAX_QUERY_ROWS,
    ) -> Dict[str, Any]:
        """Run a validated SELECT and return {columns, rows, row_count, truncated}."""
        cleaned = validate_select(sql)

        uri = f'file:{self.db_path}?mode=ro'
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(cleaned)
            except sqlite3.Error as e:
                raise SQLValidationError(f'SQL error: {e}')

            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows: List[List[Any]] = []
            truncated = False
            for i, row in enumerate(cursor):
                if i >= max_rows:
                    truncated = True
                    break
                rows.append([row[col] for col in columns])

        return {
            'columns': columns,
            'rows': rows,
            'row_count': len(rows),
            'truncated': truncated,
        }
