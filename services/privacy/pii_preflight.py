"""Pre-flight PII scan for tabular (CSV / Excel) files.

Called before indexing so the advisor can review what the system detected
and confirm — or add custom terms — before any data is written to disk.

Returns a structured report: per-sheet, per-column breakdown with the
detected role, the action that will be taken (keep / hash / drop), and
a sample of actual cell values so the advisor can spot anything the
automatic detection missed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of distinct sample values to return per column
_MAX_SAMPLES = 6
# Maximum character length for a single sample value shown in the UI
_MAX_SAMPLE_LEN = 80


def _sample_values(rows: list[dict], col: str, max_n: int = _MAX_SAMPLES) -> list[str]:
    """Return up to *max_n* distinct non-empty string values for *col*."""
    seen: list[str] = []
    lower_seen: set[str] = set()
    for row in rows:
        val = row.get(col)
        if val is None:
            continue
        s = str(val).strip()
        if not s:
            continue
        if s.lower() in lower_seen:
            continue
        lower_seen.add(s.lower())
        seen.append(s[:_MAX_SAMPLE_LEN])
        if len(seen) >= max_n:
            break
    return seen


def scan_file(
    file_path: Path,
    collection_id: str | None = None,
) -> dict[str, Any]:
    """Run a pre-flight PII scan on a CSV or Excel file.

    Does NOT modify or index the file.  Returns a report suitable for
    display in the advisor review UI.

    Return shape::

        {
          "filename": "portfolio.csv",
          "format": "csv",
          "collection_blacklist": ["SMITH FAMILY", "555-1234"],
          "sheets": [
            {
              "sheet_name": "Sheet1",
              "total_rows": 45,
              "columns": [
                {
                  "name": "Account Name",
                  "role": "account",
                  "action": "hash",
                  "sample_values": ["GERARD A SURDAKOWSKI TRUST", ...],
                  "pii_flag": true,
                  "pii_reason": "role:account"
                },
                ...
              ]
            }
          ]
        }
    """
    from services.document_extractor import DocumentExtractor
    from services.privacy.column_sanitizer import _classify_column
    from services.privacy.collection_blacklist import get_blacklist

    extractor = DocumentExtractor()
    sheets = extractor.extract_tabular_sheets(file_path)

    blacklist = get_blacklist(collection_id)

    sheet_reports: list[dict] = []
    for sheet in sheets:
        columns: list[str] = sheet.get("columns", [])
        rows: list[dict] = sheet.get("rows", [])
        role_overrides: dict[str, str] = sheet.get("role_overrides") or {}

        col_reports: list[dict] = []
        for col in columns:
            role = role_overrides.get(col)
            action = _classify_column(col, role)
            samples = _sample_values(rows, col)

            # Determine the human-readable reason for the action
            if action == "keep":
                pii_flag = False
                pii_reason = f"role:{role}" if role else "no_pii_detected"
            elif action == "hash":
                pii_flag = True
                pii_reason = f"role:{role}" if role else "name_pattern"
            else:  # drop
                pii_flag = True
                pii_reason = f"role:{role}" if role else "name_pattern"

            col_reports.append({
                "name": col,
                "role": role,
                "action": action,
                "sample_values": samples,
                "pii_flag": pii_flag,
                "pii_reason": pii_reason,
            })

        sheet_reports.append({
            "sheet_name": sheet.get("sheet_name", "Sheet1"),
            "total_rows": len(rows),
            "columns": col_reports,
        })

    ext = file_path.suffix.lower().lstrip(".")
    return {
        "filename": file_path.name,
        "format": ext,
        "collection_blacklist": blacklist,
        "sheets": sheet_reports,
    }
