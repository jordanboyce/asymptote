"""Structured data store for CSV/XLSX tabular query support.

Stores each ingested tabular document as a typed SQLite table alongside the
semantic chunk index so the chat LLM can answer aggregation/numeric questions
by running real SQL instead of scanning row-text chunks.

The table registry lives in the same SQLite database used by MetadataStore
(the collection's metadata.db) so deletion and migration stay coherent with
the rest of the indexing stack.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# --- Type inference -----------------------------------------------------------

_CURRENCY_RE = re.compile(
    r'^\s*[$€£¥₹]\s*-?[\d,]*\.?\d+\s*$'
    r'|^\s*\(?\s*[$€£¥₹]\s*[\d,]*\.?\d+\s*\)?\s*$'
    r'|^\s*-?[\d,]*\.?\d+\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|HKD|SGD)\s*$',
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r'^\s*-?[\d,]*\.?\d+\s*%\s*$')
_DATE_RE = re.compile(
    r'^\d{4}-\d{1,2}-\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?$'
    r'|^\d{1,2}/\d{1,2}/\d{2,4}$'
    r'|^\d{1,2}-[A-Za-z]{3}-\d{2,4}$'
    r'|^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}$'
)

# Financial column name → semantic role
_ROLE_PATTERNS = [
    ('ticker',        r'^(ticker|symbol|sym|security_?(id|code|symbol))$'),
    ('cusip',         r'^(cusip)$'),
    ('isin',          r'^(isin)$'),
    ('name',          r'^(name|security_?name|description|desc|holding|instrument|issuer)$'),
    ('quantity',      r'^(qty|quantity|shares|units|contracts|position|balance_units)$'),
    ('price',         r'^(price|px|last|close|mark|nav|unit_?price)$'),
    ('cost_basis',    r'^(cost|cost_?basis|book|book_?value|purchase_?price|avg_?cost|acquisition_?cost)$'),
    ('market_value',  r'^(market_?value|mv|value|notional|exposure|balance|position_?value|market_?val)$'),
    ('pnl',           r'^(pnl|p_?l|gain|gain_?loss|unrealized|realized|profit|total_?return_?dollar)$'),
    ('weight',        r'^(weight|wt|alloc|allocation|pct|percent_?of_?portfolio|port_?weight)$'),
    ('asset_class',   r'^(asset_?class|class|type|instrument_?type|category|security_?type)$'),
    ('sector',        r'^(sector|industry|gics|sub_?sector|industry_?group)$'),
    ('region',        r'^(region|country|geo|geography|domicile)$'),
    ('currency',      r'^(ccy|currency|fx|curr)$'),
    ('date',          r'^(date|as_?of|trade_?date|settle_?date|report_?date|period|valuation_?date)$'),
    ('return',        r'^(return|ret|yield|perf|performance|ytd|mtd|qtd|total_?return|rtn)$'),
    ('account',       r'^(account|acct|portfolio|fund|strategy|sleeve)$'),
    ('maturity',      r'^(maturity|maturity_?date|expiry|expiration)$'),
    ('coupon',        r'^(coupon|coupon_?rate|rate)$'),
    ('rating',        r'^(rating|credit_?rating|moody|s_?p_?rating)$'),
]

_IDENT_SAFE_RE = re.compile(r'[^A-Za-z0-9_]')


def _parse_number(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # Strip currency symbols, commas, whitespace, trailing %
    s = re.sub(r'[$€£¥₹,\s]', '', s)
    # Currency code suffix (e.g. "100 USD")
    s = re.sub(r'(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|HKD|SGD)$', '', s, flags=re.IGNORECASE)
    is_percent = s.endswith('%')
    if is_percent:
        s = s[:-1]
    # Accounting negative: (1,234) -> -1234
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        return float(s)
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
    if val is None:
        return False
    return bool(_DATE_RE.match(str(val).strip()))


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
    """Inspect a column's values and return its inferred type + metadata."""
    non_null = [v for v in values if v is not None and str(v).strip() != '']
    null_count = len(values) - len(non_null)

    if not non_null:
        return {'type': 'text', 'null_count': null_count, 'distinct_count': 0, 'stats': None}

    sample = non_null[:2000]
    all_int = True
    all_real = True
    all_currency = True
    all_percent = True
    all_date = True
    all_bool = True
    numeric_vals: List[float] = []

    for v in sample:
        s = str(v).strip()
        if not _CURRENCY_RE.match(s):
            all_currency = False
        if not _PERCENT_RE.match(s):
            all_percent = False
        if not _is_date_like(s):
            all_date = False
        if not _is_bool(v):
            all_bool = False
        num = _parse_number(s)
        if num is None:
            all_int = False
            all_real = False
        else:
            numeric_vals.append(num)
            if num != int(num):
                all_int = False

    distinct = len({str(v) for v in sample})

    if all_bool and distinct <= 3:
        return {'type': 'boolean', 'null_count': null_count, 'distinct_count': distinct, 'stats': None}
    if all_date:
        return {
            'type': 'date',
            'null_count': null_count,
            'distinct_count': distinct,
            'stats': {'min': min(str(v) for v in sample), 'max': max(str(v) for v in sample)},
        }
    if all_currency and numeric_vals:
        return {'type': 'currency', 'null_count': null_count, 'distinct_count': distinct,
                'stats': _numeric_stats(numeric_vals)}
    if all_percent and numeric_vals:
        return {'type': 'percent', 'null_count': null_count, 'distinct_count': distinct,
                'stats': _numeric_stats(numeric_vals)}
    if all_int and numeric_vals:
        return {'type': 'integer', 'null_count': null_count, 'distinct_count': distinct,
                'stats': _numeric_stats(numeric_vals)}
    if all_real and numeric_vals:
        return {'type': 'real', 'null_count': null_count, 'distinct_count': distinct,
                'stats': _numeric_stats(numeric_vals)}

    return {'type': 'text', 'null_count': null_count, 'distinct_count': distinct, 'stats': None}


def detect_financial_role(col_name: str, col_type: str) -> Optional[str]:
    """Detect the semantic financial role of a column based on its name."""
    normalized = re.sub(r'[^a-z0-9]+', '_', col_name.lower()).strip('_')
    for role, pattern in _ROLE_PATTERNS:
        if re.match(pattern, normalized):
            return role
    return None


def sanitize_identifier(name: str, fallback: str = 'col') -> str:
    """Convert an arbitrary string to a safe SQL identifier (ASCII, <=60 chars)."""
    cleaned = _IDENT_SAFE_RE.sub('_', str(name)).strip('_')
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f'_{cleaned}'
    return cleaned[:60]


# --- SQL validation ----------------------------------------------------------

_FORBIDDEN_KEYWORDS = {
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'ATTACH',
    'DETACH', 'PRAGMA', 'VACUUM', 'REINDEX', 'TRUNCATE',
    'GRANT', 'REVOKE', 'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT',
}


class SQLValidationError(ValueError):
    """Raised when a user/LLM-supplied SQL string fails validation."""


def validate_select(sql: str) -> str:
    """Validate that `sql` is a single read-only SELECT / WITH statement.

    Returns the cleaned SQL (no trailing semicolon). Raises SQLValidationError
    on any violation: multi-statement, DDL/DML keyword, or non-SELECT prefix.
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


# --- Value coercion ----------------------------------------------------------

_TYPE_TO_SQLITE = {
    'integer': 'INTEGER',
    'real': 'REAL',
    'currency': 'REAL',
    'percent': 'REAL',
    'boolean': 'INTEGER',
    'date': 'TEXT',
    'text': 'TEXT',
}


def _coerce_value(val: Any, inferred_type: str) -> Any:
    if val is None:
        return None
    if isinstance(val, float) and val != val:  # NaN
        return None
    s = str(val).strip()
    if s == '' or s.lower() in {'nan', 'null', 'none', 'n/a'}:
        return None
    if inferred_type in {'integer', 'real', 'currency', 'percent'}:
        num = _parse_number(s)
        if num is None:
            return None
        if inferred_type == 'integer':
            return int(num)
        return num
    if inferred_type == 'boolean':
        return _to_bool(val)
    return s


# --- Store -------------------------------------------------------------------

_DEFAULT_MAX_QUERY_ROWS = 1000


class StructuredStore:
    """Manages per-document typed SQL tables and their schema registry.

    Tables are stored inside the collection's metadata SQLite database using
    `csv_data_{document_id}[_sheet]` names. The `csv_schemas` registry table
    maps documents to their physical tables and caches inferred schema/roles.
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
    ) -> Dict[str, Any]:
        """Create a typed table + schema entry for an ingested tabular file."""
        if not columns:
            raise ValueError('Cannot create structured table with no columns')

        col_values: Dict[str, List[Any]] = {col: [row.get(col) for row in rows] for col in columns}

        col_infos: List[Dict[str, Any]] = []
        used_idents = set()
        for col in columns:
            info = infer_column_type(col_values[col])
            base_ident = sanitize_identifier(col, fallback='col')
            ident = base_ident
            counter = 1
            while ident.lower() in used_idents:
                counter += 1
                ident = f'{base_ident}_{counter}'
            used_idents.add(ident.lower())

            role = detect_financial_role(col, info['type'])
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
                'null_count': info['null_count'],
                'distinct_count': info['distinct_count'],
                'stats': info['stats'],
                'samples': sample_values,
            })

        table_name = self._table_name(document_id, sheet_name)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            col_defs = ['__row_number INTEGER PRIMARY KEY']
            for info in col_infos:
                sqlite_type = _TYPE_TO_SQLITE.get(info['type'], 'TEXT')
                col_defs.append(f'"{info["sql_name"]}" {sqlite_type}')
            conn.execute(f'CREATE TABLE "{table_name}" ({", ".join(col_defs)})')

            placeholders = ', '.join(['?'] * (len(col_infos) + 1))
            insert_cols_sql = '"' + '", "'.join(['__row_number'] + [c['sql_name'] for c in col_infos]) + '"'
            insert_sql = f'INSERT INTO "{table_name}" ({insert_cols_sql}) VALUES ({placeholders})'

            batch: List[List[Any]] = []
            for idx, row in enumerate(rows, start=1):
                values: List[Any] = [idx]
                for info in col_infos:
                    values.append(_coerce_value(row.get(info['name']), info['type']))
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
        return schema_payload

    def delete_document(self, document_id: str) -> int:
        """Drop all structured tables + schema rows belonging to a document."""
        dropped = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f'SELECT table_name FROM {self.SCHEMA_REGISTRY} WHERE document_id = ?',
                (document_id,),
            )
            for (table_name,) in cursor.fetchall():
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

    # --- Canned metrics ------------------------------------------------------

    AVAILABLE_METRICS: Dict[str, str] = {
        'row_count':              'Number of rows in the table',
        'total_market_value':     'Sum of the market_value column across all rows',
        'total_cost_basis':       'Sum of the cost_basis column across all rows',
        'total_pnl':              'Sum of the pnl column across all rows',
        'top_holdings':           'Top N holdings by market_value (param: limit)',
        'bottom_holdings':        'Bottom N holdings by market_value (param: limit)',
        'largest_gains':          'Top N rows by pnl (param: limit)',
        'largest_losses':         'Bottom N rows by pnl (param: limit)',
        'concentration':          'Share of total market_value held by top N positions (param: limit)',
        'breakdown_by_sector':    'Total market_value grouped by sector',
        'breakdown_by_asset_class':'Total market_value grouped by asset_class',
        'breakdown_by_region':    'Total market_value grouped by region',
        'breakdown_by_currency':  'Total market_value grouped by currency',
        'weighted_return':        'Market-value-weighted average return',
        'summary_statistics':     'Min/max/mean/sum/count for every numeric column',
    }

    def compute_metric(
        self,
        identifier: str,
        metric: str,
        limit: int = 10,
    ) -> Dict[str, Any]:
        schema = self.get_schema(identifier)
        if not schema:
            raise ValueError(f"No structured table found for '{identifier}'")

        role_to_col: Dict[str, str] = {}
        for c in schema['columns']:
            if c.get('role') and c['role'] not in role_to_col:
                role_to_col[c['role']] = c['sql_name']

        table = schema['table_name']

        def require(role: str) -> str:
            col = role_to_col.get(role)
            if not col:
                raise ValueError(
                    f"Metric '{metric}' requires a column with role '{role}' but none "
                    f"was detected in {schema['filename']}. Detected roles: "
                    f"{sorted(role_to_col.keys()) or 'none'}"
                )
            return col

        uri = f'file:{self.db_path}?mode=ro'
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row

            def scalar(sql: str, params=()) -> Any:
                cur = conn.execute(sql, params)
                row = cur.fetchone()
                return row[0] if row else None

            def fetch_all(sql: str, params=()):
                cur = conn.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return cols, [list(r) for r in cur.fetchall()]

            m = metric.lower()
            limit = max(1, min(int(limit), 1000))

            if m == 'row_count':
                return {'metric': metric, 'filename': schema['filename'],
                        'value': scalar(f'SELECT COUNT(*) FROM "{table}"')}

            if m == 'total_market_value':
                col = require('market_value')
                return {'metric': metric, 'filename': schema['filename'], 'column': col,
                        'value': scalar(f'SELECT SUM("{col}") FROM "{table}"')}

            if m == 'total_cost_basis':
                col = require('cost_basis')
                return {'metric': metric, 'filename': schema['filename'], 'column': col,
                        'value': scalar(f'SELECT SUM("{col}") FROM "{table}"')}

            if m == 'total_pnl':
                col = require('pnl')
                return {'metric': metric, 'filename': schema['filename'], 'column': col,
                        'value': scalar(f'SELECT SUM("{col}") FROM "{table}"')}

            if m in ('top_holdings', 'bottom_holdings'):
                mv = require('market_value')
                name_col = role_to_col.get('name') or role_to_col.get('ticker')
                order = 'DESC' if m == 'top_holdings' else 'ASC'
                select_cols = [f'"{mv}"']
                if name_col:
                    select_cols.insert(0, f'"{name_col}"')
                sql = (
                    f'SELECT {", ".join(select_cols)} FROM "{table}" '
                    f'WHERE "{mv}" IS NOT NULL ORDER BY "{mv}" {order} LIMIT ?'
                )
                cols, rows = fetch_all(sql, (limit,))
                return {'metric': metric, 'filename': schema['filename'],
                        'columns': cols, 'rows': rows}

            if m in ('largest_gains', 'largest_losses'):
                pnl = require('pnl')
                name_col = role_to_col.get('name') or role_to_col.get('ticker')
                order = 'DESC' if m == 'largest_gains' else 'ASC'
                select_cols = [f'"{pnl}"']
                if name_col:
                    select_cols.insert(0, f'"{name_col}"')
                sql = (
                    f'SELECT {", ".join(select_cols)} FROM "{table}" '
                    f'WHERE "{pnl}" IS NOT NULL ORDER BY "{pnl}" {order} LIMIT ?'
                )
                cols, rows = fetch_all(sql, (limit,))
                return {'metric': metric, 'filename': schema['filename'],
                        'columns': cols, 'rows': rows}

            if m == 'concentration':
                mv = require('market_value')
                total = scalar(f'SELECT SUM("{mv}") FROM "{table}"')
                top = scalar(
                    f'SELECT SUM(v) FROM (SELECT "{mv}" AS v FROM "{table}" '
                    f'WHERE "{mv}" IS NOT NULL ORDER BY "{mv}" DESC LIMIT ?)',
                    (limit,),
                )
                pct = (top / total * 100.0) if total and top is not None else None
                return {
                    'metric': metric,
                    'filename': schema['filename'],
                    'limit': limit,
                    'top_n_value': top,
                    'total_value': total,
                    'concentration_pct': pct,
                }

            if m in ('breakdown_by_sector', 'breakdown_by_asset_class',
                     'breakdown_by_region', 'breakdown_by_currency'):
                group_role = m.replace('breakdown_by_', '')
                return self._breakdown(conn, schema, group_role, 'market_value')

            if m == 'weighted_return':
                ret = require('return')
                mv = require('market_value')
                total_mv = scalar(
                    f'SELECT SUM("{mv}") FROM "{table}" WHERE "{ret}" IS NOT NULL AND "{mv}" IS NOT NULL'
                )
                weighted = scalar(
                    f'SELECT SUM("{ret}" * "{mv}") FROM "{table}" '
                    f'WHERE "{ret}" IS NOT NULL AND "{mv}" IS NOT NULL'
                )
                value = (weighted / total_mv) if total_mv else None
                return {
                    'metric': metric,
                    'filename': schema['filename'],
                    'return_column': ret,
                    'weight_column': mv,
                    'weighted_return': value,
                }

            if m == 'summary_statistics':
                stats = []
                for c in schema['columns']:
                    if c['type'] in ('integer', 'real', 'currency', 'percent'):
                        col = c['sql_name']
                        row = conn.execute(
                            f'SELECT MIN("{col}"), MAX("{col}"), AVG("{col}"), '
                            f'SUM("{col}"), COUNT("{col}") FROM "{table}"'
                        ).fetchone()
                        stats.append({
                            'column': col,
                            'role': c.get('role'),
                            'type': c['type'],
                            'min': row[0],
                            'max': row[1],
                            'avg': row[2],
                            'sum': row[3],
                            'count': row[4],
                        })
                return {'metric': metric, 'filename': schema['filename'], 'columns': stats}

            raise ValueError(
                f"Unknown metric '{metric}'. Available: {sorted(self.AVAILABLE_METRICS.keys())}"
            )

    def _breakdown(
        self,
        conn: sqlite3.Connection,
        schema: Dict[str, Any],
        group_role: str,
        value_role: str,
    ) -> Dict[str, Any]:
        table = schema['table_name']
        role_to_col = {c['role']: c['sql_name'] for c in schema['columns'] if c.get('role')}
        if group_role not in role_to_col:
            raise ValueError(
                f"No column with role '{group_role}' detected in {schema['filename']}"
            )
        if value_role not in role_to_col:
            raise ValueError(
                f"No column with role '{value_role}' detected in {schema['filename']}"
            )

        g = role_to_col[group_role]
        v = role_to_col[value_role]
        sql = (
            f'SELECT "{g}" AS group_key, SUM("{v}") AS total, COUNT(*) AS count '
            f'FROM "{table}" WHERE "{g}" IS NOT NULL GROUP BY "{g}" ORDER BY total DESC'
        )
        cur = conn.execute(sql)
        groups = [{'group': r[0], 'total': r[1], 'count': r[2]} for r in cur.fetchall()]
        total_sum = sum((g['total'] or 0) for g in groups)
        for g_row in groups:
            g_row['pct'] = (
                (g_row['total'] / total_sum * 100.0) if total_sum and g_row['total'] else None
            )
        return {
            'metric': f'breakdown_by_{group_role}',
            'filename': schema['filename'],
            'group_column': g,
            'value_column': v,
            'groups': groups,
            'total': total_sum,
        }
