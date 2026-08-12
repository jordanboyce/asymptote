"""P0.6 — sanity guards on SQL aggregates over silently-mistyped columns.

The failure this exists to prevent: a brokerage export lands with a market-value
column that numeric coercion didn't catch (an unusual currency glyph, a footnote
marker, a vendor-specific negative style), so the values are stored as TEXT.  An
agent then issues ``SELECT SUM("market_value") ...`` and SQLite *silently*
coerces each string to its leading numeric prefix::

    "1,234.56"  ->  1.0
    "$8,900.00" ->  0.0

Ten positions worth $12,345.60 sum to ``10.0``.  No error, no NULL, no hint that
anything went wrong — just a confidently wrong number handed to the model, which
reports it to the user as fact.  That is the single worst thing this product can
do, so the aggregate path refuses to return such a result unannotated.

The detection is value-based rather than schema-based on purpose: ``typeof()``
reports how each value is *actually stored*, which works identically on tables
and on the ``__by_symbol`` rollup VIEWs, and doesn't depend on declared column
affinity being meaningful.

Guards here only ever *append warnings*.  They never suppress or rewrite a
result — a caller that knows better must still be able to see the raw answer.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any, List, Sequence, Tuple

logger = logging.getLogger(__name__)


# Aggregates whose result is silently wrong (rather than merely NULL) when the
# operand is TEXT. SUM/AVG/TOTAL coerce per-value; MIN/MAX compare
# lexicographically, so "9" sorts above "10". COUNT is unaffected — omitted.
_NUMERIC_AGGREGATES = ('SUM', 'AVG', 'TOTAL', 'MIN', 'MAX')

# Matches an aggregate applied to a *bare column reference* only:
#   SUM("market value") / SUM([market value]) / SUM(market_value) / SUM(DISTINCT x)
# Deliberately does NOT match SUM(CAST(x AS REAL)) or SUM(a * b) — a caller who
# has already cast, or who is aggregating an expression, gets no warning.
_AGGREGATE_RE = re.compile(
    r'\b(' + '|'.join(_NUMERIC_AGGREGATES) + r')\s*\(\s*(?:DISTINCT\s+)?'
    r'(?:"([^"]+)"|\[([^\]]+)\]|`([^`]+)`|([A-Za-z_]\w*))\s*\)',
    re.IGNORECASE,
)

# Source tables/views referenced by FROM or JOIN.
_TABLE_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+(?:"([^"]+)"|\[([^\]]+)\]|`([^`]+)`|([A-Za-z_]\w*))',
    re.IGNORECASE,
)

# SQLite's own TEXT->number coercion: the longest leading prefix that parses as
# a number, else 0. Mirroring it exactly is what lets us distinguish "SQLite got
# this right" from "SQLite silently truncated it".
_SQLITE_PREFIX_RE = re.compile(r'^\s*[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+)')

_CURRENCY_GLYPHS = '$€£¥₹'

# How many stored values to sample per column before deciding.
_SAMPLE_SIZE = 50
# Fraction of sampled non-null values that must be TEXT-typed.
_TEXT_RATIO_THRESHOLD = 0.6


def _sqlite_numeric_value(text: str) -> float:
    """What SQLite gets when it coerces *text* in an arithmetic context."""
    m = _SQLITE_PREFIX_RE.match(text)
    if not m:
        return 0.0
    try:
        return float(m.group(0))
    except ValueError:
        return 0.0


def human_numeric_value(text: str) -> float | None:
    """What a person reading *text* in a spreadsheet would say the number is.

    Handles thousands separators, currency glyphs, accounting parens for
    negatives, and a trailing percent sign. Returns None if the string isn't a
    number at all (a ticker, a name, a date).
    """
    s = str(text).strip()
    if not s:
        return None
    negative = False
    if s.startswith('(') and s.endswith(')'):
        negative = True
        s = s[1:-1].strip()
    s = s.rstrip('%').strip()
    for glyph in _CURRENCY_GLYPHS:
        s = s.replace(glyph, '')
    s = s.replace(',', '').strip()
    if s.startswith('+'):
        s = s[1:]
    if not s or not re.fullmatch(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', s):
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def is_sum_hazard(text: str) -> bool:
    """True when SQLite's coercion of *text* disagrees with its human reading.

    ``"1,234.56"`` -> SQLite 1.0 vs human 1234.56 -> hazard.
    ``"$8,900"``   -> SQLite 0.0 vs human 8900.0  -> hazard.
    ``"(408.80)"`` -> SQLite 0.0 vs human -408.8  -> hazard.
    ``"10"``       -> SQLite 10.0 vs human 10.0   -> safe, no warning.
    """
    human = human_numeric_value(text)
    if human is None:
        return False
    return abs(human - _sqlite_numeric_value(str(text))) > 1e-9


def _identifier(match: Sequence[Any], start: int, count: int = 4) -> str:
    """Pull the first non-empty alternation group out of a regex match."""
    for i in range(start, start + count):
        if match[i]:
            return match[i]
    return ''


def extract_aggregated_columns(sql: str) -> List[Tuple[str, str]]:
    """Return ``[(aggregate_fn, column_name), ...]`` for bare-column aggregates."""
    found: List[Tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _AGGREGATE_RE.finditer(sql):
        groups = m.groups()
        column = _identifier(groups, 1)
        if not column:
            continue
        key = (groups[0].upper(), column)
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def extract_source_tables(sql: str) -> List[str]:
    """Return table/view identifiers referenced in FROM / JOIN clauses."""
    tables: List[str] = []
    for m in _TABLE_RE.finditer(sql):
        name = _identifier(m.groups(), 0)
        if name and name.upper() not in {'SELECT'} and name not in tables:
            tables.append(name)
    return tables


class _Sample:
    """Tallies from a column sample, plus a representative offending value."""

    __slots__ = ('non_null', 'text_typed', 'sum_hazards', 'numeric_texts',
                 'lengths', 'example')

    def __init__(self) -> None:
        self.non_null = 0
        self.text_typed = 0
        self.sum_hazards = 0        # values SQLite would mis-sum
        self.numeric_texts = 0      # values a human reads as numbers
        self.lengths: set[int] = set()
        self.example = ''

    @property
    def text_ratio(self) -> float:
        return self.text_typed / self.non_null if self.non_null else 0.0

    @property
    def ragged(self) -> bool:
        """Numeric strings of differing width — where lexicographic order
        diverges from numeric order ("9" > "10")."""
        return len(self.lengths) > 1


def _classify_sample(rows: Sequence[Sequence[Any]]) -> _Sample:
    """Tally a ``[(typeof(col), col), ...]`` sample."""
    sample = _Sample()
    for storage_type, value in rows:
        if value is None:
            continue
        sample.non_null += 1
        if (storage_type or '').lower() != 'text':
            continue
        sample.text_typed += 1
        as_str = str(value)
        if human_numeric_value(as_str) is None:
            continue
        sample.numeric_texts += 1
        sample.lengths.add(len(as_str.strip()))
        if is_sum_hazard(as_str):
            sample.sum_hazards += 1
            if not sample.example:
                sample.example = as_str
    return sample


def _sample_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
) -> list[tuple[Any, Any]] | None:
    """Sample stored values + storage types, or None if the pair doesn't exist."""
    safe_table = table.replace('"', '""')
    safe_column = column.replace('"', '""')
    try:
        cursor = conn.execute(
            f'SELECT typeof("{safe_column}"), "{safe_column}" '
            f'FROM "{safe_table}" WHERE "{safe_column}" IS NOT NULL '
            f'LIMIT {_SAMPLE_SIZE}'
        )
        return [(r[0], r[1]) for r in cursor.fetchall()]
    except sqlite3.Error:
        # Column lives on a different table in the join, or the identifier was
        # an alias. Not our problem to resolve — just decline to judge.
        return None


def check_aggregate_coercion(
    conn: sqlite3.Connection,
    sql: str,
) -> List[str]:
    """Inspect *sql*'s aggregates for TEXT operands; return warning strings.

    ``conn`` must be an open (read-only is fine) connection to the database the
    query ran against. Never raises — a guard that breaks the query path would
    be worse than the bug it detects.
    """
    warnings: List[str] = []
    try:
        aggregates = extract_aggregated_columns(sql)
        if not aggregates:
            return warnings
        tables = extract_source_tables(sql)
        if not tables:
            return warnings

        for func, column in aggregates:
            for table in tables:
                raw = _sample_column(conn, table, column)
                if raw is None:
                    continue  # column isn't on this table — try the next one
                sample = _classify_sample(raw)
                if sample.non_null == 0:
                    break
                if sample.text_ratio < _TEXT_RATIO_THRESHOLD:
                    break  # genuinely typed column — nothing to flag
                if sample.numeric_texts == 0:
                    break  # TEXT, but it's tickers or names — aggregating is
                           # the caller's business, not a coercion bug

                if func in ('MIN', 'MAX'):
                    # Same-width numeric strings compare identically either way,
                    # so only flag ragged widths.
                    if sample.ragged:
                        warnings.append(
                            f'aggregation_text_comparison: {func}("{column}") compared '
                            f'values stored as TEXT of differing width, so the comparison '
                            f'is alphabetical rather than numeric — "9" ranks above "10". '
                            f'THIS RESULT IS UNRELIABLE — do not report it as a figure. '
                            f'Re-run as {func}(CAST(REPLACE(REPLACE("{column}", \',\', \'\'), '
                            f'\'$\', \'\') AS REAL)).'
                        )
                elif sample.sum_hazards:
                    warnings.append(
                        f'aggregation_text_operand: {func}("{column}") aggregated values '
                        f'stored as TEXT (e.g. {sample.example!r}). SQLite coerces each '
                        f'string to its leading numeric prefix, so this result is WRONG — '
                        f'typically far too small. DO NOT report this number to the user. '
                        f'Re-run as {func}(CAST(REPLACE(REPLACE("{column}", \',\', \'\'), '
                        f'\'$\', \'\') AS REAL)), or tell the user the column needs '
                        f're-ingesting.'
                    )
                break
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning('aggregate coercion guard failed (non-fatal): %s', exc)
    return warnings


def check_null_aggregates(
    sql: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> List[str]:
    """Backstop: flag aggregate result columns that came back entirely NULL.

    Catches aliased or expression aggregates that :func:`check_aggregate_coercion`
    declines to judge. A NULL SUM over a non-empty table almost always means the
    operand column was missing or wholly non-numeric.
    """
    warnings: List[str] = []
    if not rows or not columns:
        return warnings
    try:
        if not _AGGREGATE_RE.search(sql):
            return warnings
        for idx, name in enumerate(columns):
            if not re.match(
                r'^\s*(?:' + '|'.join(_NUMERIC_AGGREGATES) + r')\s*\(',
                name,
                re.IGNORECASE,
            ):
                continue  # aliased column — can't attribute it to an aggregate
            values = [r[idx] for r in rows if idx < len(r)]
            if values and all(v is None for v in values):
                warnings.append(
                    f'aggregation_null: {name} returned NULL for every row. The '
                    f'operand column is likely absent or entirely non-numeric. '
                    f'Do not report this as zero — check the schema with '
                    f'get_table_schema first.'
                )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning('null-aggregate guard failed (non-fatal): %s', exc)
    return warnings
