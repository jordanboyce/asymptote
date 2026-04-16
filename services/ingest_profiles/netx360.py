"""NetX360 'Holdings by Investor' (HBIL) hierarchical CSV preprocessor.

The HBIL export is NOT flat tabular data.  It is a multi-account hierarchical
report with this repeating structure per account:

    Account Name,,,Account Number,,Account Type
    [optional insurance/annuity metadata block]
    ASSET,,,,,,,,TICKER,,ASSET TYPE,,MGT. NAME,,QUANTITY,,PRICE ($),VALUE ($)
    <asset rows>
    Account Total:,,,,,,,,,,,,,,,,,,"$X"

Followed by a final summary ``total`` row.

``preprocess_hbil`` converts this into a single flat DataFrame with columns:
    Account Name, Account Number, Account Type,
    Asset, Ticker, Asset Type, Manager, Quantity, Price, Value

File signature: filename matches ``HBIL*``, or row 0 contains "Report Type"
and row 1 contains "Holdings by Investor".
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Column names emitted by the preprocessor (canonical, fixed)
FLAT_COLUMNS = [
    "Account Name",
    "Account Number",
    "Account Type",
    "Asset",
    "Ticker",
    "Asset Type",
    "Manager",
    "Quantity",
    "Price",
    "Value",
]

# Markers that identify the ASSET header row
_ASSET_HEADER_MARKERS = {"asset", "ticker", "asset type", "quantity", "price ($)", "value ($)"}
_ASSET_HEADER_MIN_MATCH = 4  # at least this many markers must be present

# Rows whose first non-empty cell starts with these strings are structural rows to skip
_SKIP_ROW_PREFIXES_LOWER = (
    "account total",
    "total",
    "report type",
    "holdings by investor",
    "account name",  # account header rows are handled separately
)

_DOLLAR_RE = re.compile(r'^\$?([\d,]+\.?\d*)$')
_ACCT_NAME_ROW_RE = re.compile(r'account\s+name', re.IGNORECASE)
_ACCT_NUMBER_COL_IDX = 3  # 0-based: Account Name,,,Account Number,,Account Type
_ACCT_TYPE_COL_IDX = 5


def _is_asset_header_row(cells: List[str]) -> bool:
    """Return True if this row looks like the ASSET header row."""
    lower = {c.lower().strip() for c in cells if c.strip()}
    matches = _ASSET_HEADER_MARKERS & lower
    return len(matches) >= _ASSET_HEADER_MIN_MATCH


def _is_account_header_row(cells: List[str]) -> bool:
    """Return True if this row is an account metadata header like 'Account Name,,,Account Number,,...'"""
    non_empty = [c.strip() for c in cells if c.strip()]
    if not non_empty:
        return False
    return bool(_ACCT_NAME_ROW_RE.match(non_empty[0]))


def _is_skip_row(cells: List[str]) -> bool:
    """Return True if this is a structural row that should not become a data row."""
    non_empty = [c.strip().lower() for c in cells if c.strip()]
    if not non_empty:
        return True  # blank row → skip
    first = non_empty[0]
    return any(first.startswith(p) for p in _SKIP_ROW_PREFIXES_LOWER)


def _parse_asset_row(
    cells: List[str],
    asset_col: int,
    ticker_col: int,
    asset_type_col: int,
    mgr_col: int,
    qty_col: int,
    price_col: int,
    value_col: int,
) -> Optional[Dict[str, Any]]:
    """Extract a single asset row using the column positions from the ASSET header."""
    def _get(idx: int) -> str:
        if idx < len(cells):
            return cells[idx].strip()
        return ""

    asset = _get(asset_col)
    if not asset:
        return None  # blank asset name → skip

    # Strip totals / subtotal rows by asset name
    asset_lower = asset.lower()
    if any(asset_lower.startswith(p) for p in _SKIP_ROW_PREFIXES_LOWER):
        return None

    ticker = _get(ticker_col)
    asset_type = _get(asset_type_col)
    manager = _get(mgr_col)
    quantity_raw = _get(qty_col)
    price_raw = _get(price_col)
    value_raw = _get(value_col)

    return {
        "Asset": asset,
        "Ticker": ticker if ticker and ticker != "--" else None,
        "Asset Type": asset_type if asset_type else None,
        "Manager": manager if manager and manager != "--" else None,
        "Quantity": quantity_raw if quantity_raw else None,
        "Price": price_raw if price_raw else None,
        "Value": value_raw if value_raw else None,
    }


def _find_col_idx(header_cells: List[str], *names: str) -> int:
    """Return the 0-based index of the first cell whose stripped value is in names (case-insensitive)."""
    name_set = {n.lower() for n in names}
    for i, cell in enumerate(header_cells):
        if cell.strip().lower() in name_set:
            return i
    return -1


def is_netx360_hbil(file_path: Path) -> bool:
    """Return True if the file looks like a NetX360 Holdings by Investor export."""
    name = file_path.name.upper()
    if name.startswith("HBIL"):
        return True
    try:
        with open(file_path, encoding='utf-8', errors='replace') as fh:
            first_line = fh.readline().strip().lower()
            second_line = fh.readline().strip().lower()
        return "report type" in first_line and "holdings by investor" in second_line
    except Exception:
        return False


def preprocess_hbil(file_path: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Parse a NetX360 HBIL CSV into flat (columns, rows).

    Returns
    -------
    columns : List[str]
        The FLAT_COLUMNS list (canonical column names).
    rows : List[Dict[str, Any]]
        One dict per position, keyed by FLAT_COLUMNS.
    """
    # Read all raw rows (no header interpretation — the file is not flat)
    raw_rows: List[List[str]] = []
    with open(file_path, encoding='utf-8', errors='replace', newline='') as fh:
        reader = csv.reader(fh)
        for row in reader:
            raw_rows.append([cell for cell in row])

    flat_rows: List[Dict[str, Any]] = []

    current_account: Dict[str, Optional[str]] = {
        "Account Name": None,
        "Account Number": None,
        "Account Type": None,
    }

    # Column positions within the ASSET section (resolved per account)
    asset_col = ticker_col = asset_type_col = mgr_col = qty_col = price_col = value_col = -1
    in_asset_section = False

    for row_cells in raw_rows:
        # Pad so index lookups never throw
        cells = row_cells + [''] * max(0, 18 - len(row_cells))

        # Detect account header row: "Account Name,,,Account Number,,Account Type"
        if _is_account_header_row(cells):
            in_asset_section = False
            # The NEXT row (the data row for this account header) is parsed below on next iteration.
            # Actually the account name/number/type live in a data row immediately after the header.
            # Mark that we expect the next non-blank row to be the account data.
            # Reset; we'll pick it up when we see the account data row.
            continue

        # Detect the account data row (right after the account header)
        # Heuristic: next non-blank row after _is_account_header_row whose first cell
        # is NOT "ASSET" and NOT another structural keyword.
        # We detect it by checking if account info was just reset and this row has content.
        # Simpler: look for rows where col[3] looks like an account number (non-empty, short).
        # But many rows can satisfy that. Instead: the account data row appears immediately
        # after the "Account Name,,,Account Number,,Account Type" header row we just saw.
        # We handle this by tracking a flag.

        # Actually, let me re-read the format:
        # Row: "Account Name,,,Account Number,,Account Type"  ← header labels
        # Row: "John Q. Sample,,,SMP-001234,,Transfer On Death (Individual)"  ← actual data
        # So _is_account_header_row returns True for the labels row, and the NEXT row is data.
        # But we used `continue` above so we skip the label row and end up here on the data row.
        # We need a flag to know whether the previous row was an account header.

        # Restructure: use a state machine approach instead. Let me redo with explicit states.
        pass

    # State machine approach:
    return _parse_hbil_state_machine(raw_rows)


def _parse_hbil_state_machine(
    raw_rows: List[List[str]],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Parse HBIL rows with an explicit state machine."""

    flat_rows: List[Dict[str, Any]] = []

    STATE_PREAMBLE = "preamble"
    STATE_EXPECT_ACCOUNT_DATA = "expect_account_data"
    STATE_ACCOUNT_META = "account_meta"
    STATE_ASSET_HEADER = "asset_header"
    STATE_ASSET_DATA = "asset_data"

    state = STATE_PREAMBLE

    current_account: Dict[str, Optional[str]] = {
        "Account Name": None,
        "Account Number": None,
        "Account Type": None,
    }
    asset_col = ticker_col = asset_type_col = mgr_col = qty_col = price_col = value_col = -1

    for row_cells in raw_rows:
        cells = [c.strip() for c in row_cells]
        # Extend so index access is safe
        while len(cells) < 20:
            cells.append('')

        non_empty = [c for c in cells if c]
        if not non_empty:
            # Blank row — stay in current state (blank rows separate sections)
            continue

        first = non_empty[0].lower()

        # ── Detect account header label row (the "Account Name,,,Account Number..." row) ──
        if _is_account_header_row(cells):
            state = STATE_EXPECT_ACCOUNT_DATA
            asset_col = ticker_col = asset_type_col = mgr_col = qty_col = price_col = value_col = -1
            continue

        # ── Account data row (comes right after the account header label row) ──
        if state == STATE_EXPECT_ACCOUNT_DATA:
            current_account["Account Name"] = cells[0] if cells[0] else None
            current_account["Account Number"] = cells[_ACCT_NUMBER_COL_IDX] if cells[_ACCT_NUMBER_COL_IDX] else None
            current_account["Account Type"] = cells[_ACCT_TYPE_COL_IDX] if cells[_ACCT_TYPE_COL_IDX] else None
            state = STATE_ACCOUNT_META
            continue

        # ── ASSET header row ──
        if _is_asset_header_row(cells):
            # Resolve column positions from this header
            asset_col = _find_col_idx(cells, "asset")
            ticker_col = _find_col_idx(cells, "ticker")
            asset_type_col = _find_col_idx(cells, "asset type")
            mgr_col = _find_col_idx(cells, "mgt. name", "mgt name", "manager")
            qty_col = _find_col_idx(cells, "quantity")
            price_col = _find_col_idx(cells, "price ($)", "price")
            value_col = _find_col_idx(cells, "value ($)", "value")
            state = STATE_ASSET_DATA
            continue

        # ── In asset data section ──
        if state == STATE_ASSET_DATA:
            # Skip Account Total rows, final total rows, and other structural rows
            if _is_skip_row(cells):
                continue

            if asset_col < 0:
                continue  # no asset header resolved yet → skip

            parsed = _parse_asset_row(
                cells,
                asset_col=asset_col,
                ticker_col=ticker_col,
                asset_type_col=asset_type_col,
                mgr_col=mgr_col,
                qty_col=qty_col,
                price_col=price_col,
                value_col=value_col,
            )
            if parsed is None:
                continue

            row: Dict[str, Any] = {}
            row["Account Name"] = current_account["Account Name"]
            row["Account Number"] = current_account["Account Number"]
            row["Account Type"] = current_account["Account Type"]
            row.update(parsed)
            flat_rows.append(row)
            continue

        # ── Preamble / account meta ── skip structural rows
        # (e.g. "Report Type", "Holdings by Investor", insurance metadata)
        # These are simply skipped in STATE_PREAMBLE / STATE_ACCOUNT_META

    logger.info(
        f"NetX360 HBIL preprocessor: extracted {len(flat_rows)} position rows "
        f"across accounts"
    )
    return FLAT_COLUMNS, flat_rows
