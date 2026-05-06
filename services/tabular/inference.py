"""Generic type inference for tabular data.

Pure functions: given a column's raw values, infer one of the built-in types
(``boolean``, ``date``, ``integer``, ``real``, ``text``) plus per-column
metadata (null count, distinct count, numeric stats). Callers may pass extra
``TypeExtension`` instances to recognise format-specific types like currency
or percent — :class:`services.financial.holdings_store.HoldingsStore` does
this to detect financial number formats during ingest.

Nothing in this Module knows about SQLite, brokerage data, or the wider
ingest pipeline. The seam is small on purpose: inference here, persistence
elsewhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

import pandas as pd


_DATE_RE = re.compile(
    r'^\d{4}-\d{1,2}-\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?$'
    r'|^\d{1,2}/\d{1,2}/\d{2,4}$'
    r'|^\d{1,2}-[A-Za-z]{3}-\d{2,4}$'
    r'|^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}$'
)

_IDENT_SAFE_RE = re.compile(r'[^A-Za-z0-9_]')

# Strings that indicate a missing/unavailable value in brokerage/bank exports.
# Treated as NULL during both type inference and value coercion.
NULL_MARKERS = frozenset({
    '', '--', '-', 'n/a', 'na', 'nan', 'null', 'none', 'nil', '—', '–',
    'n.a.', 'n.a', '#n/a', '#na', '#null', '#value!', '#ref!', '#div/0!',
    'provide', 'missing', 'unknown', 'tbd', 'tba', 'undefined',
})

# K/M/B multiplier suffix regex (e.g. "1.5K" → 1500, "2.3M" → 2_300_000)
_KMB_RE = re.compile(r'^(-?[\d,]*\.?\d+)\s*([kmb])$', re.IGNORECASE)

# Inferred types whose original string form is worth preserving alongside
# the coerced value (so callers can show the user the input verbatim).
RAW_PRESERVING_TYPES = frozenset({'integer', 'real', 'currency', 'percent', 'date'})

# Maps inferred core type names to SQLite column affinities. Extension types
# contribute their own affinities via TypeExtension.sqlite_type.
CORE_TYPE_TO_SQLITE: Dict[str, str] = {
    'integer': 'INTEGER',
    'real':    'REAL',
    'boolean': 'INTEGER',
    'date':    'TEXT',
    'text':    'TEXT',
}


@dataclass
class TypeExtension:
    """A custom column type that callers can layer onto the generic inference.

    Attributes
    ----------
    name:
        The type string stored in schema JSON, e.g. ``'currency'``.
    sqlite_type:
        The SQLite affinity to use (``'REAL'``, ``'TEXT'``, etc.).
    detector:
        Called with a single stripped string value; returns ``True`` when
        that value matches the type. ``infer_column_type`` promotes a column
        to this type when ≥80% of non-null sample values match.
    coerce:
        Called with a single raw value; returns the coerced storage value
        (or ``None`` if the value cannot be parsed).
    """
    name: str
    sqlite_type: str
    detector: Callable[[str], bool]
    coerce: Callable[[Any], Optional[Any]]


def parse_generic_number(s: str) -> Optional[float]:
    """Parse a numeric string including common financial formats.

    Handles:
    - Thousands commas: ``"1,234.56"`` → ``1234.56``
    - Accounting-negative parens: ``"(1,234)"`` → ``-1234``
    - K/M/B suffix: ``"1.5K"`` → ``1500``, ``"2.3M"`` → ``2_300_000``
    - Currency symbols and ISO codes are NOT stripped here — those are
      handled by a TypeExtension's coerce function.
    """
    if not s:
        return None
    s_lower = s.lower()
    if s_lower in NULL_MARKERS:
        return None

    negative = False
    if s.startswith('(') and s.endswith(')'):
        negative = True
        s = s[1:-1]

    cleaned = s.replace(',', '').strip()

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
    return parse_date_like(val) is not None


def parse_date_like(val: Any) -> Optional[str]:
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
        if not raw or raw.lower() in NULL_MARKERS:
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


def infer_column_type(
    values: List[Any],
    type_extensions: Sequence[TypeExtension] = (),
) -> Dict[str, Any]:
    """Inspect a column's values and return its inferred type + metadata.

    Uses an 80% threshold: if ≥80% of non-null values satisfy a type's
    criteria, the whole column is promoted to that type. Non-conforming
    minority values become NULL at storage time. This correctly handles
    brokerage columns that mix numeric cells with sentinel strings like
    ``'--'``, ``'N/A'``, or ``'Provide'``.

    Priority order:
      1. Boolean  (100% match, ≤3 distinct values)
      2. Date     (≥80%)
      3. Type extensions  (≥80% — e.g. currency, percent)
      4. Integer  (≥80%, all parsed values are whole numbers)
      5. Real     (≥80%)
      6. Text     (fallback)
    """
    non_null = [
        v for v in values
        if v is not None and str(v).strip().lower() not in NULL_MARKERS
    ]
    null_count = len(values) - len(non_null)

    if not non_null:
        return {'type': 'text', 'null_count': null_count, 'distinct_count': 0, 'stats': None}

    sample = non_null[:2000]
    sample_strs = [str(v).strip() for v in sample]
    n = len(sample)
    threshold = 0.80
    distinct = len(set(sample_strs))

    if all(_is_bool(v) for v in sample) and distinct <= 3:
        return {'type': 'boolean', 'null_count': null_count, 'distinct_count': distinct, 'stats': None}

    date_count = sum(1 for s in sample_strs if _is_date_like(s))
    if date_count / n >= threshold:
        parsed_dates = [d for d in (parse_date_like(s) for s in sample_strs) if d is not None]
        return {
            'type': 'date',
            'null_count': null_count,
            'distinct_count': distinct,
            'stats': {'min': min(parsed_dates), 'max': max(parsed_dates)} if parsed_dates else None,
        }

    for ext in type_extensions:
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

    # Fallback when no single extension hit 80% on its own (e.g. a column
    # mixing "$1,591.20" and "(408.80)" — currency matches once, generic
    # matches once, neither clears threshold individually). Any value that
    # parses as a number via either path counts toward promotion.
    numeric_vals: List[float] = []
    int_count = 0
    had_extension_match = False
    for s in sample_strs:
        num: Optional[float] = parse_generic_number(s)
        if num is None:
            for ext in type_extensions:
                val = ext.coerce(s)
                if isinstance(val, (int, float)):
                    num = float(val)
                    had_extension_match = True
                    break
        if num is not None:
            numeric_vals.append(num)
            if num == int(num):
                int_count += 1

    numeric_frac = len(numeric_vals) / n
    if numeric_frac >= threshold:
        # If any value needed extension coercion (currency / percent formatting),
        # classify as real — an "integer" column shouldn't quietly absorb
        # currency-formatted cells even if they happen to be whole dollars.
        if int_count == len(numeric_vals) and not had_extension_match:
            return {'type': 'integer', 'null_count': null_count, 'distinct_count': distinct,
                    'stats': _numeric_stats(numeric_vals)}
        return {'type': 'real', 'null_count': null_count, 'distinct_count': distinct,
                'stats': _numeric_stats(numeric_vals)}

    return {'type': 'text', 'null_count': null_count, 'distinct_count': distinct, 'stats': None}


def sanitize_identifier(name: str, fallback: str = 'col') -> str:
    """Convert an arbitrary string to a safe SQL identifier (ASCII, <=60 chars)."""
    cleaned = _IDENT_SAFE_RE.sub('_', str(name)).strip('_')
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f'_{cleaned}'
    return cleaned[:60]


def coerce_value(
    val: Any,
    inferred_type: str,
    type_extensions: Sequence[TypeExtension] = (),
) -> Any:
    """Coerce a raw value to its storage form for the given inferred type."""
    if val is None:
        return None
    if isinstance(val, float) and val != val:
        return None
    s = str(val).strip()
    if not s or s.lower() in NULL_MARKERS:
        return None

    if inferred_type in {'integer', 'real'}:
        num = parse_generic_number(s)
        if num is None:
            # Fall back to any extension coercer. Covers the case where a
            # 'real' column holds currency-formatted cells (e.g. "$1,591.20")
            # that parse_generic_number intentionally ignores.
            for ext in type_extensions:
                ext_val = ext.coerce(s)
                if isinstance(ext_val, (int, float)):
                    num = float(ext_val)
                    break
        if num is None:
            return None
        return int(num) if inferred_type == 'integer' else num

    if inferred_type == 'boolean':
        return _to_bool(val)

    if inferred_type == 'date':
        return parse_date_like(val)

    for ext in type_extensions:
        if ext.name == inferred_type:
            return ext.coerce(val)

    return s


def raw_storage_value(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, float) and val != val:
        return None
    s = str(val).strip()
    if not s or s.lower() in NULL_MARKERS:
        return None
    return s


def should_preserve_raw(values: List[Any], inferred_type: str) -> bool:
    if inferred_type not in RAW_PRESERVING_TYPES:
        return False
    return any(raw_storage_value(v) is not None for v in values)
