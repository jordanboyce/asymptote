"""Per-collection custom PII term blacklist.

Advisors can flag any text fragment — a client name, an address fragment,
an account number — and it will be stripped from every file indexed into
that collection from that point on.

Storage: app_db user preferences (same pattern as redaction profiles).
Key format: ``pii_blacklist:{collection_id}``
Value: list of raw strings (case-insensitive matching at apply time).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from services.app_database import app_db

logger = logging.getLogger(__name__)

_KEY_PREFIX = "pii_blacklist"


def _pref_key(collection_id: str | None) -> str:
    return f"{_KEY_PREFIX}:{collection_id or '__global__'}"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def get_blacklist(collection_id: str | None) -> list[str]:
    """Return the custom PII terms for a collection (empty list if none)."""
    stored = app_db.get_user_preference(_pref_key(collection_id), None)
    if isinstance(stored, list):
        return [t for t in stored if isinstance(t, str) and t.strip()]
    return []


def add_terms(collection_id: str | None, terms: list[str]) -> list[str]:
    """Add one or more terms to the blacklist. Ignores duplicates (case-insensitive)."""
    current = get_blacklist(collection_id)
    lower_existing = {t.lower() for t in current}
    added = []
    for term in terms:
        term = term.strip()
        if not term:
            continue
        if term.lower() not in lower_existing:
            current.append(term)
            lower_existing.add(term.lower())
            added.append(term)

    app_db.set_user_preference(_pref_key(collection_id), current)
    if added:
        logger.info("PII blacklist for %r: added %d term(s): %s",
                    collection_id, len(added), added)
    return current


def remove_terms(collection_id: str | None, terms: list[str]) -> list[str]:
    """Remove one or more terms from the blacklist (case-insensitive match)."""
    lower_remove = {t.strip().lower() for t in terms}
    current = [t for t in get_blacklist(collection_id)
               if t.lower() not in lower_remove]
    app_db.set_user_preference(_pref_key(collection_id), current)
    return current


def clear_blacklist(collection_id: str | None) -> None:
    """Remove all custom terms for a collection."""
    app_db.set_user_preference(_pref_key(collection_id), [])


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

def apply_blacklist(text: str, blacklist: list[str]) -> str:
    """Replace any blacklisted term found in *text* with [REDACTED].

    Matching is case-insensitive and whole-token aware (uses word boundaries
    where the term is a single word, substring match for multi-word phrases).
    """
    if not blacklist or not text:
        return text

    for term in blacklist:
        term = term.strip()
        if not term:
            continue
        # Use word boundary only for single-word terms to avoid breaking
        # multi-word phrases like "909 E Northern Ave"
        if re.search(r"\s", term):
            pattern = re.compile(re.escape(term), re.IGNORECASE)
        else:
            pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
        text = pattern.sub("[REDACTED]", text)

    return text
