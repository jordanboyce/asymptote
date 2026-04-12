"""Financial type detection and number parsing.

Handles currency symbols, accounting-negative notation, percent formatting,
ISO currency code suffixes, and K/M/B multiplier suffixes.  Registers
'currency' and 'percent' as typed extensions with the generic StructuredStore
via register_type_extension().
"""

from __future__ import annotations

import re
from typing import Any, Optional

_CURRENCY_RE = re.compile(
    r'^\s*[$€£¥₹]\s*-?[\d,]*\.?\d+\s*$'
    r'|^\s*\(?\s*[$€£¥₹]\s*[\d,]*\.?\d+\s*\)?\s*$'
    r'|^\s*-?[\d,]*\.?\d+\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|HKD|SGD)\s*$',
    re.IGNORECASE,
)

_PERCENT_RE = re.compile(r'^\s*-?[\d,]*\.?\d+\s*%\s*$')

_KMB_RE = re.compile(r'^(-?[\d,]*\.?\d+)\s*([kmb])$', re.IGNORECASE)

# Null-marker strings treated as missing (not parse failures)
_NULL_MARKERS = frozenset({
    '', '--', '-', 'n/a', 'na', 'nan', 'null', 'none', 'nil', '—', '–',
    'n.a.', 'n.a', '#n/a', '#na', '#null', '#value!', '#ref!', '#div/0!',
    'provide', 'missing', 'unknown', 'tbd', 'tba', 'undefined',
})


def _parse_number(val: Any) -> Optional[float]:
    """Parse a financial number string to float.

    Handles:
    - Currency symbols: ``$``, ``€``, ``£``, etc.
    - Thousands commas: ``"1,234.56"`` → ``1234.56``
    - ISO currency code suffix: ``"100 USD"`` → ``100.0``
    - Accounting-negative: ``"(1,234.56)"`` or ``"$(1,234.56)"`` → ``-1234.56``
    - Percent sign: stripped (caller decides whether to divide by 100)
    - K/M/B suffix: ``"1.5K"`` → ``1500.0``, ``"2.3M"`` → ``2_300_000.0``
    - Null markers (``'-'``, ``'N/A'``, ``'Provide'``, etc.) → ``None``
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in _NULL_MARKERS:
        return None

    # Detect accounting negative BEFORE stripping parens
    negative = False
    if s.startswith('(') and s.endswith(')'):
        negative = True
        s = s[1:-1].strip()

    # Strip currency symbols and commas
    s = re.sub(r'[$€£¥₹,\s]', '', s)
    # Strip ISO currency code suffix (e.g. "100 USD" → "100")
    s = re.sub(r'(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|HKD|SGD)$', '', s, flags=re.IGNORECASE).strip()

    # Trailing percent sign — strip it (value is returned as-is, not /100)
    if s.endswith('%'):
        s = s[:-1].strip()

    # K/M/B multiplier suffix
    kmb = _KMB_RE.match(s)
    if kmb:
        base_str = kmb.group(1).replace(',', '')
        suffix = kmb.group(2).upper()
        try:
            base = float(base_str)
        except ValueError:
            return None
        multiplier = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}[suffix]
        result = base * multiplier
        return -result if negative else result

    try:
        result = float(s)
        return -result if negative else result
    except ValueError:
        return None


def _detect_currency(s: str) -> bool:
    """Return True if *s* looks like a currency-formatted value."""
    return bool(_CURRENCY_RE.match(s))


def _detect_percent(s: str) -> bool:
    """Return True if *s* looks like a percentage value."""
    return bool(_PERCENT_RE.match(s))


def register() -> None:
    """Register currency and percent type extensions with the structured store."""
    from services.structured_store import TypeExtension, register_type_extension

    register_type_extension(TypeExtension(
        name='currency',
        sqlite_type='REAL',
        detector=_detect_currency,
        coerce=_parse_number,
    ))
    register_type_extension(TypeExtension(
        name='percent',
        sqlite_type='REAL',
        detector=_detect_percent,
        coerce=_parse_number,
    ))
