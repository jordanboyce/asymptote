"""Per-collection redaction profiles and configuration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from services.app_database import app_db

logger = logging.getLogger(__name__)


class RedactionStyle(str, Enum):
    """How redacted entities are replaced in output text."""
    REDACTED = "redacted"                       # [REDACTED]
    ENTITY_TYPE = "entity_type"                 # [ACCOUNT_NUMBER], [PERSON]
    CONSISTENT_PSEUDONYM = "consistent_pseudonym"  # John Smith -> Alex Morgan
    PARTIAL_MASK = "partial_mask"               # ****1234
    SYNTHETIC_PLACEHOLDER = "synthetic_placeholder"  # random-but-valid-format


# Default entity types that Presidio supports + our custom ones
DEFAULT_ENTITY_TYPES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "US_SSN",
    "US_DRIVER_LICENSE",
    "US_PASSPORT",
    "US_BANK_NUMBER",
    "DATE_TIME",
    "LOCATION",
    "NRP",
    "MEDICAL_LICENSE",
    "URL",
    # Custom financial recognizers
    "FINANCIAL_ACCOUNT",
    "ROUTING_NUMBER",
    "CUSIP_IN_CONTEXT",
]


@dataclass
class RedactionProfile:
    """Redaction configuration for a single collection."""
    collection_id: str | None = None
    redaction_style: RedactionStyle = RedactionStyle.ENTITY_TYPE
    entity_types_enabled: list[str] | None = None  # None = all
    allow_list: list[str] = field(default_factory=list)
    minimum_score_threshold: float = 0.4
    strict_mode: bool = True
    # Per-entity-type style overrides: {"PERSON": "consistent_pseudonym"}
    entity_style_overrides: dict[str, str] = field(default_factory=dict)
    # Per-entity-type minimum score overrides (higher = fewer false positives)
    entity_score_thresholds: dict[str, float] = field(default_factory=dict)

    def style_for_entity(self, entity_type: str) -> RedactionStyle:
        override = self.entity_style_overrides.get(entity_type)
        if override:
            try:
                return RedactionStyle(override)
            except ValueError:
                pass
        return self.redaction_style


# Global allow-list of financial terms that Presidio misidentifies as PII.
# Merged into every profile so these are never redacted regardless of config.
_FINANCIAL_ALLOW_LIST = [
    # Temporal words misidentified as DATE_TIME
    "Annual", "Monthly", "Daily", "Weekly", "Quarterly", "Yearly",
    "YTD", "MTD", "QTD",
    # Short words / compound terms misidentified as PERSON
    "Max", "Min", "Max Drawdown", "Max Loss",
    # Fund family / brand names misidentified as PERSON
    "Vanguard", "Fidelity", "Schwab", "BlackRock", "PIMCO",
    "iShares", "SPDR", "Invesco", "Putnam", "Franklin",
    "Templeton", "Nuveen", "Calvert",
    # Tickers that look like names
    "MUB", "MAX", "KIM", "LEE", "RAY", "BILL", "MARK", "JOSH",
    "ADAM", "JACK", "CHAD", "TROY", "ROSS", "REED", "DREW",
    "FORD", "ALLY", "DELL",
    # Greek letters / financial metrics misidentified as NRP or PERSON
    "Drawdown", "Sharpe", "Sortino", "Treynor",
    "Alpha", "Beta", "Sigma", "Delta", "Gamma", "Theta", "Vega", "Rho",
]

# In-memory cache of loaded profiles
_profile_cache: dict[str | None, RedactionProfile] = {}

# Default profile for collections without explicit config
_DEFAULT_PROFILE = RedactionProfile(
    redaction_style=RedactionStyle.ENTITY_TYPE,
    minimum_score_threshold=0.4,
    strict_mode=True,
    # PHONE_NUMBER at 0.4 fires on decimal numbers; DATE_TIME at 0.4 fires
    # on words like "Annual" and "Monthly". Raise these.
    entity_score_thresholds={"PHONE_NUMBER": 0.7, "DATE_TIME": 0.6},
)


def _preference_key(collection_id: str | None) -> str:
    cid = collection_id or "__global__"
    return f"redaction_profile:{cid}"


def get_redaction_profile(collection_id: str | None = None) -> RedactionProfile:
    """Load the redaction profile for a collection (or global default)."""
    if collection_id in _profile_cache:
        return _profile_cache[collection_id]

    stored = app_db.get_user_preference(_preference_key(collection_id), None)
    if stored and isinstance(stored, dict):
        profile = _dict_to_profile(stored, collection_id)
    else:
        profile = RedactionProfile(
            collection_id=collection_id,
            redaction_style=_DEFAULT_PROFILE.redaction_style,
            entity_types_enabled=_DEFAULT_PROFILE.entity_types_enabled,
            allow_list=list(_DEFAULT_PROFILE.allow_list),
            minimum_score_threshold=_DEFAULT_PROFILE.minimum_score_threshold,
            strict_mode=_DEFAULT_PROFILE.strict_mode,
        )

    # Merge global financial allow-list into every profile
    merged = set(a.lower() for a in _FINANCIAL_ALLOW_LIST) | set(a.lower() for a in profile.allow_list)
    profile.allow_list = list(merged)

    # Merge default per-entity score thresholds (profile-specific overrides win)
    effective_thresholds = dict(_DEFAULT_PROFILE.entity_score_thresholds)
    effective_thresholds.update(profile.entity_score_thresholds)
    profile.entity_score_thresholds = effective_thresholds

    _profile_cache[collection_id] = profile
    return profile


def save_redaction_profile(
    collection_id: str | None,
    updates: dict[str, Any],
) -> RedactionProfile:
    """Update and persist a redaction profile."""
    profile = get_redaction_profile(collection_id)

    if "redaction_style" in updates:
        try:
            profile.redaction_style = RedactionStyle(updates["redaction_style"])
        except ValueError:
            logger.warning(f"Invalid redaction_style: {updates['redaction_style']}")

    if "entity_types_enabled" in updates:
        val = updates["entity_types_enabled"]
        profile.entity_types_enabled = val if isinstance(val, list) else None

    if "allow_list" in updates:
        val = updates["allow_list"]
        profile.allow_list = val if isinstance(val, list) else []

    if "minimum_score_threshold" in updates:
        profile.minimum_score_threshold = max(0.0, min(1.0, float(updates["minimum_score_threshold"])))

    if "strict_mode" in updates:
        profile.strict_mode = bool(updates["strict_mode"])

    if "entity_style_overrides" in updates:
        val = updates["entity_style_overrides"]
        profile.entity_style_overrides = val if isinstance(val, dict) else {}

    if "entity_score_thresholds" in updates:
        val = updates["entity_score_thresholds"]
        profile.entity_score_thresholds = val if isinstance(val, dict) else {}

    app_db.set_user_preference(_preference_key(collection_id), _profile_to_dict(profile))
    _profile_cache[collection_id] = profile
    return profile


def clear_profile_cache() -> None:
    """Clear the in-memory profile cache (for testing)."""
    _profile_cache.clear()


def _profile_to_dict(profile: RedactionProfile) -> dict[str, Any]:
    return {
        "redaction_style": profile.redaction_style.value,
        "entity_types_enabled": profile.entity_types_enabled,
        "allow_list": profile.allow_list,
        "minimum_score_threshold": profile.minimum_score_threshold,
        "strict_mode": profile.strict_mode,
        "entity_style_overrides": profile.entity_style_overrides,
        "entity_score_thresholds": profile.entity_score_thresholds,
    }


def _dict_to_profile(d: dict[str, Any], collection_id: str | None) -> RedactionProfile:
    style = RedactionStyle.ENTITY_TYPE
    if "redaction_style" in d:
        try:
            style = RedactionStyle(d["redaction_style"])
        except ValueError:
            pass

    return RedactionProfile(
        collection_id=collection_id,
        redaction_style=style,
        entity_types_enabled=d.get("entity_types_enabled"),
        allow_list=d.get("allow_list", []),
        minimum_score_threshold=float(d.get("minimum_score_threshold", 0.4)),
        strict_mode=bool(d.get("strict_mode", True)),
        entity_style_overrides=d.get("entity_style_overrides", {}),
        entity_score_thresholds=d.get("entity_score_thresholds", {}),
    )
