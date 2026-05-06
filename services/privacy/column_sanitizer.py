"""Index-time PII column sanitization for tabular (CSV / Excel) data.

When a financial CSV is indexed, column values are stored raw in both the
vector store (chunk text) and the structured SQL store.  This module strips
PII-bearing columns at ingest time — before anything is persisted — so
client identity information is never written to disk in the index.

Philosophy
----------
The advisor importing the file already has access to every name, address,
and account number in their source system (the brokerage portal, their CRM,
etc.).  The AI assistant only needs the *financial* data: positions, market
values, tickers, gains/losses, sector allocations, etc.

Two complementary layers are applied:

1. **Role-based** — columns whose detected semantic role is explicitly a
   PII role (``account_number``, ``account``) are sanitized regardless of
   their column name.

2. **Name-based** — columns with no detected financial role but whose name
   matches a PII heuristic (address, phone, zip, ssn, email, …) are also
   sanitized.

Sanitization strategy
---------------------
* ``account_number`` role  → replaced with a short deterministic hash
  (e.g. ``[ACCT-3F2A]``) so per-account grouping is preserved without
  exposing the real number.
* ``account`` role (holder name) → same deterministic hash so multi-account
  files still show distinct accounts.
* Other PII columns (address, phone, etc.) → value is dropped entirely
  (replaced with ``""``), because there is no legitimate reason for those
  to appear in AI context at all.

The row_texts (plain-text rendering of each row) are regenerated after
sanitization so they match the sanitized values.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Roles that are PII and must be sanitized.
# ---------------------------------------------------------------------------

# These are replaced with a consistent short hash so grouping / aggregation
# still works (e.g. "how many accounts does this client have?").
_HASH_ROLES: frozenset[str] = frozenset({
    "account",         # account holder name / account label
    "account_number",  # brokerage account number
})

# These are dropped entirely — no useful financial information in them.
_DROP_ROLES: frozenset[str] = frozenset({
    "address",
    "phone",
    "email",
    "ssn",
    "tax_id",
    "dob",
    "birth_date",
    "zip",
    "postal_code",
    "city",
    "state",
    "country_code",   # kept only when it is a 2-letter ISO code via ticker context
    "rep_name",       # advisor rep name — not needed for AI context
    "client_name",    # explicit client name role
    "contact",
})

# ---------------------------------------------------------------------------
# Name-based heuristics for columns that have no detected financial role.
# Any column whose normalized name matches one of these patterns is treated
# as a PII column and dropped entirely.
# ---------------------------------------------------------------------------

_PII_NAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Identity
    (re.compile(r"\b(ssn|social.?sec|tax.?id|ein|tin)\b", re.IGNORECASE), "drop"),
    (re.compile(r"\b(dob|birth.?date|date.?of.?birth)\b", re.IGNORECASE), "drop"),
    (re.compile(r"\b(passport|driver.?lic|license.?num)\b", re.IGNORECASE), "drop"),
    # Contact
    (re.compile(r"\b(phone|mobile|cell|fax|telephone)\b", re.IGNORECASE), "drop"),
    (re.compile(r"\b(email|e.?mail)\b", re.IGNORECASE), "drop"),
    # Address components
    (re.compile(r"\b(address|addr|street|city|zip|postal|province|state)\b", re.IGNORECASE), "drop"),
    # Account holder / client name — but NOT "security_name", "asset_name", "fund_name"
    (re.compile(
        r"(?<!\bsecurity_)(?<!\basset_)(?<!\bfund_)(?<!\binstrument_)"
        r"\b(client.?name|account.?holder|holder.?name|owner.?name|rep.?name|advisor.?name)\b",
        re.IGNORECASE,
    ), "drop"),
    # Account numbers — hash these to preserve grouping. Pattern covers
    # the long form ("Account Number"), short form ("Acct Num"), camelCase
    # ("AccountNumber"), snake/kebab ("account_no", "client-id"), and the
    # variants seen in real custodian exports.
    (re.compile(r"\b(?:account|acct|client)[\s_\-]*(?:number|num|no|id)\b", re.IGNORECASE), "hash"),
]

# Financial roles that are explicitly *safe* — never sanitized even if their
# column name contains an ambiguous word.
_SAFE_ROLES: frozenset[str] = frozenset({
    "ticker", "cusip", "isin", "name", "quantity", "price",
    "cost_basis", "market_value", "pnl", "weight", "asset_class",
    "sector", "region", "currency", "date", "return",
    "maturity", "coupon", "rating", "manager",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _short_hash(value: str) -> str:
    """Return a short deterministic 4-hex-char hash of *value*."""
    digest = hashlib.sha256(value.encode()).hexdigest()[:4].upper()
    return f"[ACCT-{digest}]"


def _normalize_col_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _classify_column(col_name: str, role: str | None) -> str:
    """Return 'keep', 'hash', or 'drop' for a column.

    Priority:
      1. If the role is a known financial-safe role → keep.
      2. If the role is a known PII hash role → hash.
      3. If the role is a known PII drop role → drop.
      4. No financial role: check name-based heuristics.
      5. Default: keep (we don't want false positives on financial data).
    """
    if role and role in _SAFE_ROLES:
        return "keep"
    if role and role in _HASH_ROLES:
        return "hash"
    if role and role in _DROP_ROLES:
        return "drop"

    # Name-based heuristics only apply when there is no recognized financial role
    if not role or role not in _SAFE_ROLES:
        for pattern, action in _PII_NAME_PATTERNS:
            if pattern.search(col_name):
                return action

    return "keep"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sanitize_tabular_sheet(
    sheet: dict[str, Any],
    role_overrides: dict[str, str] | None = None,
    blacklist: list[str] | None = None,
) -> dict[str, Any]:
    """Sanitize PII columns in a sheet dict in-place and return it.

    ``sheet`` has the structure produced by ``DocumentExtractor.extract_tabular_sheets``:
      {
        'sheet_name': str,
        'columns': [str, ...],
        'rows': [dict, ...],        # raw cell values
        'row_texts': [str, ...],    # plain-text renderings
        'role_overrides': {col: role},
      }

    ``blacklist`` is an optional list of custom terms the advisor flagged
    during pre-flight review. Any cell value in a kept column that contains
    a blacklisted term is also redacted.

    After this call:
    * PII column values in ``rows`` are replaced (hashed or emptied).
    * Blacklisted terms in kept-column values are replaced with [REDACTED].
    * ``row_texts`` is regenerated to match the sanitized values.
    * A summary of what was removed is logged at INFO level.
    """
    from services.financial.roles import detect_financial_role
    from services.privacy.collection_blacklist import apply_blacklist

    columns: list[str] = sheet.get("columns", [])
    rows: list[dict] = sheet.get("rows", [])
    combined_overrides: dict[str, str] = dict(sheet.get("role_overrides") or {})
    if role_overrides:
        combined_overrides.update(role_overrides)

    # Determine action per column
    col_actions: dict[str, str] = {}
    for col in columns:
        role = combined_overrides.get(col) or detect_financial_role(col, "text")
        col_actions[col] = _classify_column(col, role)

    hash_cols = [c for c, a in col_actions.items() if a == "hash"]
    drop_cols = [c for c, a in col_actions.items() if a == "drop"]
    keep_cols = [c for c, a in col_actions.items() if a == "keep"]

    has_work = hash_cols or drop_cols or blacklist
    if not has_work:
        return sheet

    logger.info(
        "sanitize_tabular_sheet '%s': hashing %d col(s) %s, dropping %d col(s) %s, "
        "blacklist terms=%d",
        sheet.get("sheet_name", "?"),
        len(hash_cols), hash_cols,
        len(drop_cols), drop_cols,
        len(blacklist) if blacklist else 0,
    )

    # Sanitize each row dict
    for row in rows:
        for col in hash_cols:
            if col in row and row[col] is not None:
                row[col] = _short_hash(str(row[col]))
        for col in drop_cols:
            if col in row:
                row[col] = ""
        # Apply custom blacklist to kept columns
        if blacklist:
            for col in keep_cols:
                if col in row and row[col] is not None:
                    row[col] = apply_blacklist(str(row[col]), blacklist)

    # Regenerate row_texts to match sanitized values
    sheet["row_texts"] = [_row_to_text(row, columns, col_actions) for row in rows]

    return sheet


def _row_to_text(
    row: dict[str, Any],
    columns: list[str],
    col_actions: dict[str, str],
) -> str:
    """Render a sanitized row as a plain-text string for the vector index.

    Only columns with action 'keep' or 'hash' are included.  Dropped columns
    are omitted entirely so their empty strings don't clutter the embedding.
    """
    parts: list[str] = []
    for col in columns:
        action = col_actions.get(col, "keep")
        if action == "drop":
            continue
        val = row.get(col)
        if val is None or val == "":
            continue
        parts.append(f"{col}: {val}")
    return " | ".join(parts)
