"""Structured data store for CSV/XLSX tabular query support.

Stores each ingested tabular document as a typed SQLite table alongside the
semantic chunk index so the chat LLM can answer aggregation/numeric questions
by running real SQL instead of scanning row-text chunks.

The table registry lives in the same SQLite database used by MetadataStore
(the collection's metadata.db) so deletion and migration stay coherent with
the rest of the indexing stack.

Extension points
----------------
External modules (e.g. ``services.financial``) can register additional type
detectors and column-role detectors at import time:

  * ``register_type_extension(TypeExtension(...))``  — adds a custom type that
    is checked during column inference with the same 80% threshold used for
    built-in types.
  * ``register_role_detector(fn)``  — adds a function ``(col_name, col_type)
    → Optional[str]`` that maps column names to semantic roles.

Both registries are module-level lists; registration is idempotent in the
sense that Python only executes each ``import`` once per interpreter session.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plugin registry
# ---------------------------------------------------------------------------

@dataclass
class TypeExtension:
    """A custom column type registered by an external plugin.

    Attributes
    ----------
    name:
        The type string stored in schema JSON, e.g. ``'currency'``.
    sqlite_type:
        The SQLite affinity to use (``'REAL'``, ``'TEXT'``, etc.).
    detector:
        Called with a single stripped string value; returns ``True`` when
        that value matches the type.  ``infer_column_type`` promotes a column
        to this type when ≥80% of non-null sample values match.
    coerce:
        Called with a single raw value; returns the coerced storage value
        (or ``None`` if the value cannot be parsed).
    """
    name: str
    sqlite_type: str
    detector: Callable[[str], bool]
    coerce: Callable[[Any], Optional[Any]]


_type_extensions: List[TypeExtension] = []
_role_detectors: List[Callable[[str, str], Optional[str]]] = []


def register_type_extension(ext: TypeExtension) -> None:
    """Register a custom type detector/coercer (called by plugins at import time)."""
    _type_extensions.append(ext)


def register_role_detector(fn: Callable[[str, str], Optional[str]]) -> None:
    """Register a column role detector (called by plugins at import time)."""
    _role_detectors.append(fn)


def detect_column_role(col_name: str, col_type: str) -> Optional[str]:
    """Dispatch to all registered role detectors; return the first match."""
    for fn in _role_detectors:
        role = fn(col_name, col_type)
        if role:
            return role
    return None


# ---------------------------------------------------------------------------
# Generic type inference helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(
    r'^\d{4}-\d{1,2}-\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?$'
    r'|^\d{1,2}/\d{1,2}/\d{2,4}$'
    r'|^\d{1,2}-[A-Za-z]{3}-\d{2,4}$'
    r'|^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}$'
)

_IDENT_SAFE_RE = re.compile(r'[^A-Za-z0-9_]')

# Strings that indicate a missing/unavailable value in brokerage/bank exports.
# Treated as NULL during both type inference and value coercion.
_NULL_MARKERS = frozenset({
    '', '--', '-', 'n/a', 'na', 'nan', 'null', 'none', 'nil', '—', '–',
    'n.a.', 'n.a', '#n/a', '#na', '#null', '#value!', '#ref!', '#div/0!',
    'provide', 'missing', 'unknown', 'tbd', 'tba', 'undefined',
})

# K/M/B multiplier suffix regex (e.g. "1.5K" → 1500, "2.3M" → 2_300_000)
_KMB_RE = re.compile(r'^(-?[\d,]*\.?\d+)\s*([kmb])$', re.IGNORECASE)
_RAW_PRESERVING_TYPES = frozenset({'integer', 'real', 'currency', 'percent', 'date'})


def _parse_generic_number(s: str) -> Optional[float]:
    """Parse a numeric string including common financial formats.

    Handles:
    - Thousands commas: ``"1,234.56"`` → ``1234.56``
    - Accounting-negative parens: ``"(1,234)"`` → ``-1234``
    - K/M/B suffix: ``"1.5K"`` → ``1500``, ``"2.3M"`` → ``2_300_000``
    - Currency symbols and ISO codes are NOT stripped here — those are
      handled by the financial type extension's coerce function.
    """
    if not s:
        return None
    s_lower = s.lower()
    if s_lower in _NULL_MARKERS:
        return None

    negative = False
    if s.startswith('(') and s.endswith(')'):
        negative = True
        s = s[1:-1]

    cleaned = s.replace(',', '').strip()

    # K/M/B multiplier
    kmb = _KMB_RE.match(cleaned)
    if kmb:
        base_str, suffix = kmb.group(1).replace(',', ''), kmb.group(2).upper()
        try:
            base = float(base_str)
        except ValueError:
            return None
        multiplier = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}[suffix]
        result = base * multiplier
        return -result if negative else result

    try:
        result = float(cleaned)
        return -result if negative else result
    except ValueError:
        return None


def _is_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return True
    if val is None:
        return False
    return str(val).strip().lower() in {'true', 'false', 'yes', 'no', 'y', 'n'}


def _to_bool(val: Any) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, bool):
        return 1 if val else 0
    s = str(val).strip().lower()
    if s in {'true', 'yes', 'y', '1'}:
        return 1
    if s in {'false', 'no', 'n', '0'}:
        return 0
    return None


def _is_date_like(val: Any) -> bool:
    return _parse_date_like(val) is not None


def _parse_date_like(val: Any) -> Optional[str]:
    """Parse a date-like value and normalize it to an ISO string."""
    if val is None:
        return None
    if isinstance(val, float) and val != val:
        return None
    if isinstance(val, datetime):
        parsed = val
        raw = val.isoformat()
    elif isinstance(val, date):
        return val.isoformat()
    else:
        raw = str(val).strip()
        if not raw or raw.lower() in _NULL_MARKERS:
            return None
        has_date_signals = (
            bool(_DATE_RE.match(raw))
            or bool(re.search(r'\d{1,4}[/-]\d{1,2}[/-]\d{1,4}', raw))
            or bool(re.search(r'[A-Za-z]{3,}', raw))
        )
        if not has_date_signals:
            return None
        parsed = pd.to_datetime(raw, errors='coerce')
        if pd.isna(parsed):
            return None
        if hasattr(parsed, "to_pydatetime"):
            parsed = parsed.to_pydatetime()

    has_time = bool(re.search(r'\d:\d|am\b|pm\b', raw, flags=re.IGNORECASE))
    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0 and parsed.microsecond == 0 and not has_time:
        return parsed.date().isoformat()
    return parsed.replace(microsecond=0).isoformat()


def _numeric_stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    total = sum(sorted_vals)
    mean = total / n

    def percentile(p: float) -> float:
        k = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
        return sorted_vals[k]

    return {
        'min': sorted_vals[0],
        'max': sorted_vals[-1],
        'mean': mean,
        'sum': total,
        'p25': percentile(25),
        'p50': percentile(50),
        'p75': percentile(75),
        'count': n,
    }


def infer_column_type(values: List[Any]) -> Dict[str, Any]:
    """Inspect a column's values and return its inferred type + metadata.

    Uses an 80% threshold: if ≥80% of non-null values satisfy a type's
    criteria, the whole column is promoted to that type.  Non-conforming
    minority values become NULL at storage time.  This correctly handles
    brokerage columns that mix numeric cells with sentinel strings like
    ``'--'``, ``'N/A'``, or ``'Provide'``.

    Priority order:
      1. Boolean  (100% match, ≤3 distinct values)
      2. Date     (≥80%)
      3. Registered type extensions  (≥80% — e.g. currency, percent)
      4. Integer  (≥80%, all parsed values are whole numbers)
      5. Real     (≥80%)
      6. Text     (fallback)
    """
    # Common null markers count as missing, not as type-breaking values
    non_null = [
        v for v in values
        if v is not None and str(v).strip().lower() not in _NULL_MARKERS
    ]
    null_count = len(values) - len(non_null)

    if not non_null:
        return {'type': 'text', 'null_count': null_count, 'distinct_count': 0, 'stats': None}

    sample = non_null[:2000]
    sample_strs = [str(v).strip() for v in sample]
    n = len(sample)
    threshold = 0.80
    distinct = len(set(sample_strs))

    # 1. Boolean (100% match, ≤3 distinct)
    if all(_is_bool(v) for v in sample) and distinct <= 3:
        return {'type': 'boolean', 'null_count': null_count, 'distinct_count': distinct, 'stats': None}

    # 2. Date (≥80%)
    date_count = sum(1 for s in sample_strs if _is_date_like(s))
    if date_count / n >= threshold:
        parsed_dates = [d for d in (_parse_date_like(s) for s in sample_strs) if d is not None]
        return {
            'type': 'date',
            'null_count': null_count,
            'distinct_count': distinct,
            'stats': {'min': min(parsed_dates), 'max': max(parsed_dates)} if parsed_dates else None,
        }

    # 3. Registered type extensions (≥80%)
    for ext in _type_extensions:
        match_count = sum(1 for s in sample_strs if ext.detector(s))
        if match_count / n >= threshold:
            coerced = [ext.coerce(v) for v in sample]
            numeric_vals = [c for c in coerced if c is not None]
            if numeric_vals:
                return {
                    'type': ext.name,
                    'null_count': null_count,
                    'distinct_count': distinct,
                    'stats': _numeric_stats(numeric_vals),
                }

    # 4 & 5. Generic numeric (≥80%)
    numeric_vals: List[float] = []
    int_count = 0
    for s in sample_strs:
        num = _parse_generic_number(s)
        if num is not None:
            numeric_vals.append(num)
            if num == int(num):
                int_count += 1

    numeric_frac = len(numeric_vals) / n
    if numeric_frac >= threshold:
        if int_count == len(numeric_vals):
            return {'type': 'integer', 'null_count': null_count, 'distinct_count': distinct,
                    'stats': _numeric_stats(numeric_vals)}
        return {'type': 'real', 'null_count': null_count, 'distinct_count': distinct,
                'stats': _numeric_stats(numeric_vals)}

    # 6. Text fallback
    return {'type': 'text', 'null_count': null_count, 'distinct_count': distinct, 'stats': None}


def sanitize_identifier(name: str, fallback: str = 'col') -> str:
    """Convert an arbitrary string to a safe SQL identifier (ASCII, <=60 chars)."""
    cleaned = _IDENT_SAFE_RE.sub('_', str(name)).strip('_')
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f'_{cleaned}'
    return cleaned[:60]


# ---------------------------------------------------------------------------
# SQL validation
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYWORDS = {
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'ATTACH',
    'DETACH', 'PRAGMA', 'VACUUM', 'REINDEX', 'TRUNCATE',
    'GRANT', 'REVOKE', 'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT',
}


class SQLValidationError(ValueError):
    """Raised when a user/LLM-supplied SQL string fails validation."""


def validate_select(sql: str) -> str:
    """Validate that *sql* is a single read-only SELECT / WITH statement.

    Returns the cleaned SQL (no trailing semicolon). Raises
    :class:`SQLValidationError` on any violation.
    """
    if not sql or not sql.strip():
        raise SQLValidationError('SQL query is empty')

    cleaned = sql.strip().rstrip(';').strip()
    if ';' in cleaned:
        raise SQLValidationError('Multiple statements are not allowed')

    # Blank out quoted string literals so keywords inside them don't trip the check
    stripped = re.sub(r"'([^'\\]|\\.)*'", "''", cleaned)
    stripped = re.sub(r'"([^"\\]|\\.)*"', '""', stripped)
    upper = stripped.upper()

    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf'\b{kw}\b', upper):
            raise SQLValidationError(f'Forbidden keyword: {kw}')

    first_word = re.match(r'^\s*(\w+)', upper)
    if not first_word or first_word.group(1) not in {'SELECT', 'WITH'}:
        raise SQLValidationError('Only SELECT / WITH queries are allowed')

    return cleaned


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

# Maps inferred type names to SQLite column affinities.
# Plugin types are added dynamically from _type_extensions at create_table time.
_TYPE_TO_SQLITE: Dict[str, str] = {
    'integer': 'INTEGER',
    'real':    'REAL',
    'boolean': 'INTEGER',
    'date':    'TEXT',
    'text':    'TEXT',
}


def _coerce_value(val: Any, inferred_type: str) -> Any:
    """Coerce a raw value to its storage form for the given inferred type."""
    if val is None:
        return None
    if isinstance(val, float) and val != val:  # NaN
        return None
    s = str(val).strip()
    if not s or s.lower() in _NULL_MARKERS:
        return None

    if inferred_type in {'integer', 'real'}:
        num = _parse_generic_number(s)
        if num is None:
            return None
        return int(num) if inferred_type == 'integer' else num

    if inferred_type == 'boolean':
        return _to_bool(val)

    if inferred_type == 'date':
        return _parse_date_like(val)

    # Delegate to the registered extension coercer for plugin types
    for ext in _type_extensions:
        if ext.name == inferred_type:
            return ext.coerce(val)

    # date and text: store as string
    return s


def _raw_storage_value(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, float) and val != val:
        return None
    s = str(val).strip()
    if not s or s.lower() in _NULL_MARKERS:
        return None
    return s


def _should_preserve_raw(values: List[Any], inferred_type: str) -> bool:
    if inferred_type not in _RAW_PRESERVING_TYPES:
        return False
    return any(_raw_storage_value(v) is not None for v in values)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

_DEFAULT_MAX_QUERY_ROWS = 1000


class StructuredStore:
    """Manages per-document typed SQL tables and their schema registry.

    Tables are stored inside the collection's metadata SQLite database using
    ``csv_data_{document_id}[_sheet]`` names. The ``csv_schemas`` registry
    table maps documents to their physical tables and caches inferred
    schema/roles.

    This class is intentionally domain-agnostic. Financial-specific behaviour
    (currency/percent types, column role detection, portfolio metrics) is
    provided by the ``services.financial`` plugin which registers itself with
    :func:`register_type_extension` and :func:`register_role_detector`.
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
            Overrides the heuristic role detector result.  Supplied by the
            vendor profile system (P0.4).
        type_overrides:
            Optional mapping of original column name → type string
            (``'real'``, ``'currency'``, ``'percent'``, ``'date'``, etc.).
            Overrides the heuristic ``infer_column_type`` result.  Supplied
            by the vendor profile system (P0.4).
        """
        if not columns:
            raise ValueError('Cannot create structured table with no columns')

        role_overrides = role_overrides or {}
        type_overrides = type_overrides or {}

        col_values: Dict[str, List[Any]] = {col: [row.get(col) for row in rows] for col in columns}

        col_infos: List[Dict[str, Any]] = []
        used_idents = set()
        for col in columns:
            info = infer_column_type(col_values[col])
            # P0.4: apply profile type override if present
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
            if _should_preserve_raw(col_values[col], info['type']):
                raw_ident = f'{ident}__raw'
                raw_counter = 1
                while raw_ident.lower() in used_idents:
                    raw_counter += 1
                    raw_ident = f'{ident}__raw_{raw_counter}'
                used_idents.add(raw_ident.lower())
                raw_sql_name = raw_ident

            # P0.4: profile role override takes precedence over heuristic
            role = role_overrides.get(col) or detect_column_role(col, info['type'])
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
                'raw_sql_name': raw_sql_name,
                'null_count': info['null_count'],
                'distinct_count': info['distinct_count'],
                'stats': info['stats'],
                'samples': sample_values,
            })

        # -- P0.5: LLM-assisted role inference for unmapped columns ----------
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
                        collection_id=None,  # collection_id not available here
                    )
                    for c in col_infos:
                        if not c['role'] and c['name'] in llm_roles:
                            c['role'] = llm_roles[c['name']]
                except Exception as exc:
                    logger.warning("LLM schema inference failed, continuing without: %s", exc)
        # -----------------------------------------------------------------

        table_name = self._table_name(document_id, sheet_name)

        # Build SQLite affinity map from core types + registered extensions
        type_to_sqlite = dict(_TYPE_TO_SQLITE)
        for ext in _type_extensions:
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
                    values.append(_coerce_value(raw_value, info['type']))
                    if info.get('raw_sql_name'):
                        values.append(_raw_storage_value(raw_value))
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
            f"Created structured table {table_name}: {len(rows)} rows × {len(col_infos)} cols "
            f"(roles: {sorted(set(financial_roles.values())) or 'none'})"
        )

        # P0.3: auto-create a by-symbol rollup view when a symbol/ticker role is detected.
        # Brokerage files typically have one row per tax lot; the view aggregates to positions.
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
        """Create a ``<table>__by_symbol`` VIEW aggregating lot-level rows per symbol.

        SUMs market_value, cost_basis, pnl, and quantity across lots; COUNTs
        lot rows; and computes a weighted-average unit cost when both cost_basis
        and quantity are present. Non-numeric / non-role columns are omitted to
        avoid ambiguous GROUP BY semantics.
        """
        SUMMABLE_ROLES = {'market_value', 'cost_basis', 'pnl', 'quantity'}
        FIRST_ROLES = {'name', 'asset_class', 'sector', 'region', 'currency', 'account', 'rating'}

        select_parts: List[str] = [f'"{symbol_col}" AS "{symbol_col}"']
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

        view_name = f'{table_name}__by_symbol'
        sql = (
            f'CREATE VIEW IF NOT EXISTS "{view_name}" AS\n'
            f'SELECT {", ".join(select_parts)}\n'
            f'FROM "{table_name}"\n'
            f'GROUP BY "{symbol_col}"'
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f'DROP VIEW IF EXISTS "{view_name}"')
            conn.execute(sql)
            conn.commit()
        logger.info(f"Created rollup view {view_name} (grouping by '{symbol_col}')")

    def delete_document(self, document_id: str) -> int:
        """Drop all structured tables + schema rows belonging to a document."""
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
            logger.info(f'Dropped {dropped} structured table(s) for document {document_id}')
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
                        }
                        for c in schema['columns']
                    ],
                    'financial_roles': roles,
                })
            return result

    def get_schema(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Look up a table schema by table_name, filename, or document_id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
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
