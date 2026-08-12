"""The Boundary Report — what actually crossed to the external model.

The audit log already records every redaction, but it records them the way a
log does: one flat row per detected entity, newest first, with no sense of
*where* in the pipeline it fired or which rows describe the same client. A
compliance officer reading it learns that 34 things were replaced. They do not
learn that "Robert Henderson" — one person, mentioned nine times across two
retrieved transcript passages — left this machine as "Marcus Whitfield".

This module turns the log into that second statement. It groups events by the
boundary they fired at, collapses repeated mentions of the same value into one
substitution with an occurrence count, and — only when the caller explicitly
asks — pairs each replacement with the original it stood in for.

**On the originals.** ``original_text`` is the one column in the audit log that
is real PII. It is stored locally, it is never sent to a model, and it is the
entire point of the demo: the advisor has to be able to see the real name in
their own record sitting next to the stand-in that crossed the wire. So it is
available here, but never by default — the caller passes ``reveal=True``, which
makes the disclosure an explicit act at every layer rather than a property of
the endpoint. Nothing in this module calls an AI provider or leaves the process.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Boundaries ───────────────────────────────────────────────────────────────
#
# The audit log's `tool_name` column is overloaded: the MCP path writes the
# tool's name, and the text path writes a source label (`chat_context`,
# `notes_output`). Both are "where this fired", so we classify rather than
# rename — an older log row keeps working.

_SOURCE_LABEL_BOUNDARY = {
    "chat_context": "retrieved_text",
    "notes_output": "generated_text",
    "followup_output": "generated_text",
}

BOUNDARY_DEFS: list[dict[str, str]] = [
    {
        "key": "retrieved_text",
        "label": "Passages from your documents",
        "description": (
            "Text pulled out of your files to answer the question. This travels "
            "inside the prompt, so it is scrubbed before the prompt is built — "
            "not after."
        ),
    },
    {
        "key": "tool_result",
        "label": "Answers to the model's own questions",
        "description": (
            "When the model asks Finn for holdings, search hits, or meeting "
            "notes, the reply is scrubbed on its way out of the process."
        ),
    },
    {
        "key": "generated_text",
        "label": "Drafts on the way back",
        "description": (
            "Prose the model wrote — a Note of Record, a follow-up email — is "
            "checked a second time, in case it echoed an identifier that "
            "reached it some other way."
        ),
    },
    {
        "key": "other",
        "label": "Other",
        "description": "Redactions recorded without a source label.",
    },
]

_BOUNDARY_BY_KEY = {b["key"]: b for b in BOUNDARY_DEFS}

# The third control in COMPLIANCE.md's boundary table has nothing to show here,
# and saying so is more honest than an empty section. Tabular columns holding
# client identifiers are dropped at ingest, so there is no crossing to log —
# the PII never reaches disk in the first place.
UNLOGGED_CONTROLS: list[dict[str, str]] = [
    {
        "label": "Client columns in spreadsheets",
        "description": (
            "Account holder names and account numbers in an uploaded export "
            "are removed as the file is read, before anything is stored. "
            "Nothing appears below for these because there is no crossing to "
            "record — the data never lands."
        ),
    },
]


def classify_boundary(tool_name: str | None) -> str:
    """Map an audit-log ``tool_name`` onto one of the boundary keys."""
    if not tool_name:
        return "other"
    return _SOURCE_LABEL_BOUNDARY.get(tool_name, "tool_result")


# ── Entity labels ────────────────────────────────────────────────────────────
#
# Presidio's entity names are for engineers. A compliance officer reading this
# panel should not have to know what NRP stands for.

_ENTITY_LABELS = {
    "PERSON": "Name",
    "TRUST_ACCOUNT_NAME": "Trust or account title",
    "EMAIL_ADDRESS": "Email address",
    "PHONE_NUMBER": "Phone number",
    "US_SSN": "Social Security number",
    "US_ITIN": "Taxpayer ID",
    "US_DRIVER_LICENSE": "Driver's licence",
    "US_PASSPORT": "Passport number",
    "US_BANK_NUMBER": "Bank account number",
    "FINANCIAL_ACCOUNT": "Account number",
    "ROUTING_NUMBER": "Routing number",
    "CUSIP_IN_CONTEXT": "Security identifier",
    "IBAN_CODE": "IBAN",
    "CREDIT_CARD": "Card number",
    "US_ADDRESS": "Street address",
    "LOCATION": "Place",
    "DATE_TIME": "Date",
    "NRP": "Nationality or affiliation",
    "IP_ADDRESS": "IP address",
    "URL": "Web address",
    "MEDICAL_LICENSE": "Medical licence",
    "CRYPTO": "Crypto wallet",
}


def entity_label(entity_type: str | None) -> str:
    if not entity_type:
        return "Identifier"
    known = _ENTITY_LABELS.get(entity_type.upper())
    if known:
        return known
    return entity_type.replace("_", " ").strip().capitalize()


# ── Report ───────────────────────────────────────────────────────────────────


def _headline(enabled: bool, available: bool, total: int, distinct: int) -> str:
    if not enabled:
        return (
            "Redaction is switched off. Client identifiers can reach the AI "
            "provider as written."
        )
    if not available:
        return (
            "The redaction engine could not start, so nothing is being "
            "scrubbed. Treat this as redaction being off."
        )
    if total == 0:
        return "Nothing has needed redacting in this window."
    values = f"{distinct} identifier{'' if distinct == 1 else 's'}"
    mentions = f"{total} mention{'' if total == 1 else 's'}"
    if total == distinct:
        return f"{values} were replaced before anything reached the AI provider."
    return (
        f"{values} were replaced before anything reached the AI provider, "
        f"across {mentions}."
    )


def build_boundary_report(
    *,
    collection_id: str | None = None,
    session_id: str | None = None,
    since: str | None = None,
    reveal: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    """Assemble the Boundary Report for a collection / session / time window.

    ``reveal`` controls one thing only: whether each substitution carries the
    ``original`` it replaced. Everything else about the response is identical,
    so the panel can render the shape once and light up the left-hand column
    when the advisor asks for it.
    """
    from config import settings

    enabled = bool(getattr(settings, "enable_pii_redaction", False))

    engine_available = False
    style = None
    try:
        from services.privacy.redaction_engine import redaction_engine

        engine_available = bool(redaction_engine.available)
    except Exception as e:  # pragma: no cover - import-time environment issue
        logger.warning("Boundary report could not reach the redaction engine: %s", e)

    try:
        from services.privacy.redaction_config import get_redaction_profile

        style = get_redaction_profile(collection_id).redaction_style.value
    except Exception as e:  # pragma: no cover - config store unavailable
        logger.warning("Boundary report could not read the redaction profile: %s", e)

    from services.privacy.redaction_log import redaction_log

    events = redaction_log.get_recent(
        session_id=session_id,
        collection_id=collection_id,
        since=since,
        limit=max(1, min(int(limit), 500)),
    )

    # (boundary, entity_type, original, replacement) is the identity of a
    # substitution. Keying on the original as well as the replacement matters
    # under `entity_type` style, where every name collapses to "[PERSON]" —
    # without the original, three different clients would merge into one row
    # and the count would read as one person mentioned three times.
    grouped: dict[str, dict[str, Any]] = {}

    for e in events:
        key = classify_boundary(e.get("tool_name"))
        bucket = grouped.setdefault(
            key,
            {"count": 0, "tools": {}, "subs": {}},
        )
        bucket["count"] += 1

        tool = e.get("tool_name")
        if tool:
            bucket["tools"][tool] = bucket["tools"].get(tool, 0) + 1

        original = e.get("original_text") or ""
        replacement = e.get("replacement") or ""
        entity_type = e.get("entity_type") or ""
        timestamp = e.get("timestamp") or ""

        sub_key = (entity_type, original, replacement)
        sub = bucket["subs"].get(sub_key)
        if sub is None:
            sub = {
                "entity_type": entity_type,
                "entity_label": entity_label(entity_type),
                "replacement": replacement,
                "occurrences": 0,
                "first_seen": timestamp,
                "last_seen": timestamp,
                "score": float(e.get("score") or 0.0),
            }
            if reveal:
                sub["original"] = original
            bucket["subs"][sub_key] = sub
        sub["occurrences"] += 1
        if timestamp:
            if not sub["first_seen"] or timestamp < sub["first_seen"]:
                sub["first_seen"] = timestamp
            if timestamp > sub["last_seen"]:
                sub["last_seen"] = timestamp
        sub["score"] = max(sub["score"], float(e.get("score") or 0.0))

    boundaries: list[dict[str, Any]] = []
    for definition in BOUNDARY_DEFS:
        bucket = grouped.get(definition["key"])
        if not bucket:
            continue
        subs = sorted(
            bucket["subs"].values(),
            key=lambda s: (-s["occurrences"], s["entity_label"], s["replacement"]),
        )
        boundaries.append({
            **definition,
            "count": bucket["count"],
            "distinct_count": len(subs),
            "tools": sorted(bucket["tools"].keys()),
            "substitutions": subs,
        })

    total = sum(b["count"] for b in boundaries)
    distinct = sum(b["distinct_count"] for b in boundaries)

    return {
        "collection_id": collection_id,
        "session_id": session_id,
        "since": since,
        "enabled": enabled,
        "engine_available": engine_available,
        "redaction_style": style,
        "originals_included": bool(reveal),
        "headline": _headline(enabled, engine_available, total, distinct),
        "total_redactions": total,
        "distinct_values": distinct,
        "boundaries": boundaries,
        "unlogged_controls": list(UNLOGGED_CONTROLS),
        "truncated": len(events) >= max(1, min(int(limit), 500)),
    }
