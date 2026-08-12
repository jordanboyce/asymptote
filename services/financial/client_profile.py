"""Per-Collection client profile / IPS store (v4.6).

The typed cousin of the v4.3 free-text collection guide. The guide tells the
model how to *read* a collection; the profile tells it what the portfolio is
supposed to look like — target allocation, concentration ceilings, prohibited
holdings, tax posture, liquidity needs — so drift becomes arithmetic instead
of opinion.

Storage mirrors :class:`services.meeting_notes.MeetingNotesStore`: one table
in the Collection's own ``metadata.db``, one row per collection, with the
canonical payload in a ``raw_json`` column and a few columns promoted for
cheap filtering.

Every field is optional. A half-filled profile is the normal case, so
:func:`profile_completeness` reports which sections are populated *and* names
what meeting prep cannot say without the missing ones — callers degrade
section by section rather than guessing.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.schemas import ClientProfile

logger = logging.getLogger(__name__)


# Sections, the profile fields that populate them, and — the point of this
# table — the sentence prep cannot say when the section is empty.
_SECTION_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("allocation_targets", "Cannot report allocation drift — no IPS target allocation set."),
    ("concentration_limit", "Cannot flag concentration against the client's own ceiling — no max single position set."),
    ("prohibited_holdings", "Cannot check for prohibited holdings — no exclusion list set."),
    ("risk_tolerance", "Cannot frame drift against stated risk tolerance — not recorded."),
    ("goals", "Cannot tie the portfolio to funded goals — none recorded."),
    ("liquidity", "Cannot judge whether the cash balance is intentional — no cash reserve target set."),
    ("tax", "Cannot size a harvest against the client's bracket — no tax profile set."),
    ("household", "Cannot name who is in the household — no members recorded."),
)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ClientProfileStore:
    """SQLite store for one client profile per Collection.

    Shares the Collection's ``metadata.db`` with :class:`MetadataStore`,
    :class:`HoldingsStore`, and :class:`MeetingNotesStore`; owns only its
    own table.
    """

    TABLE = "client_profile"

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id TEXT NOT NULL UNIQUE,
                    display_name TEXT,
                    risk_tolerance TEXT,
                    next_review_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                )
            """)
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.TABLE}_collection "
                f"ON {self.TABLE}(collection_id)"
            )
            conn.commit()

    # ── read ──────────────────────────────────────────────────────────────

    def get(self, collection_id: str) -> Optional[dict[str, Any]]:
        """Return the stored profile dict, or None when none has been saved."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"SELECT * FROM {self.TABLE} WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["raw_json"])
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "client_profile: unreadable raw_json for collection %s — treating as absent",
                collection_id,
            )
            return None
        payload["updated_at"] = row["updated_at"]
        payload["created_at"] = row["created_at"]
        return payload

    def get_model(self, collection_id: str) -> Optional[ClientProfile]:
        """Return the profile as a validated :class:`ClientProfile`, or None.

        A stored payload that no longer validates (schema drift across an
        upgrade) is dropped to None rather than raising — prep degrades to
        "no profile" instead of 500ing the whole page.
        """
        raw = self.get(collection_id)
        if raw is None:
            return None
        try:
            return ClientProfile.model_validate(raw)
        except Exception as exc:
            logger.warning(
                "client_profile: stored payload for %s failed validation (%s) — treating as absent",
                collection_id, exc,
            )
            return None

    def exists(self, collection_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT 1 FROM {self.TABLE} WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        return row is not None

    # ── write ─────────────────────────────────────────────────────────────

    def save(self, collection_id: str, profile: ClientProfile) -> dict[str, Any]:
        """Insert or replace this collection's profile. Returns the stored dict."""
        if not collection_id:
            raise ValueError("collection_id required to save a client profile")

        payload = profile.model_dump(mode="json", exclude_none=False)
        now = _utc_now()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""
                INSERT INTO {self.TABLE} (
                    collection_id, display_name, risk_tolerance,
                    next_review_date, created_at, updated_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_id) DO UPDATE SET
                    display_name     = excluded.display_name,
                    risk_tolerance   = excluded.risk_tolerance,
                    next_review_date = excluded.next_review_date,
                    updated_at       = excluded.updated_at,
                    raw_json         = excluded.raw_json
                """,
                (
                    collection_id,
                    profile.display_name,
                    profile.risk_tolerance.value if profile.risk_tolerance else None,
                    profile.next_review_date,
                    now,
                    now,
                    json.dumps(payload),
                ),
            )
            conn.commit()

        stored = self.get(collection_id)
        return stored if stored is not None else payload

    def delete(self, collection_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"DELETE FROM {self.TABLE} WHERE collection_id = ?",
                (collection_id,),
            )
            conn.commit()
            return cur.rowcount > 0


# ── completeness ──────────────────────────────────────────────────────────


def _section_populated(profile: ClientProfile, section: str) -> bool:
    if section == "allocation_targets":
        return bool(profile.ips.allocation_targets)
    if section == "concentration_limit":
        return profile.ips.max_single_position_pct is not None
    if section == "prohibited_holdings":
        return bool(profile.ips.prohibited_holdings)
    if section == "risk_tolerance":
        return profile.risk_tolerance is not None
    if section == "goals":
        return bool(profile.goals)
    if section == "liquidity":
        return profile.liquidity.cash_reserve_target is not None
    if section == "tax":
        return any(
            v is not None
            for v in (
                profile.tax.filing_status,
                profile.tax.federal_bracket_pct,
                profile.tax.state,
                profile.tax.capital_loss_carryforward,
            )
        )
    if section == "household":
        return bool(profile.household_members)
    return False


def profile_completeness(profile: Optional[ClientProfile]) -> dict[str, Any]:
    """Report which sections are populated and what prep cannot say without them.

    The ``blocked`` list is the honest half: it is what the advisor would
    otherwise assume prep had checked and silently skipped.
    """
    if profile is None:
        return {
            "score": 0.0,
            "populated": [],
            "missing": [s for s, _ in _SECTION_REQUIREMENTS],
            "blocked": [reason for _, reason in _SECTION_REQUIREMENTS],
            "note": (
                "No client profile saved for this collection. Meeting prep will "
                "report the portfolio as-is but cannot compare it to anything."
            ),
        }

    populated: list[str] = []
    missing: list[str] = []
    blocked: list[str] = []
    for section, reason in _SECTION_REQUIREMENTS:
        if _section_populated(profile, section):
            populated.append(section)
        else:
            missing.append(section)
            blocked.append(reason)

    total = len(_SECTION_REQUIREMENTS)
    score = round(len(populated) / total, 2) if total else 0.0

    result: dict[str, Any] = {
        "score": score,
        "populated": populated,
        "missing": missing,
        "blocked": blocked,
    }
    if not missing:
        result["note"] = "Profile complete — every prep section has something to compare against."
    return result


def allocation_targets_sum(profile: ClientProfile) -> Optional[float]:
    """Sum of target percentages, or None when no targets are set.

    Surfaced rather than corrected: a set of targets summing to 87% is a
    data-entry error the advisor should see, not something to normalize away
    behind their back.
    """
    targets = profile.ips.allocation_targets
    if not targets:
        return None
    return round(sum(t.target_pct for t in targets), 2)
