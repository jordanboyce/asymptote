"""Daily advisor morning brief — *what to do today* email.

The companion to :mod:`services.digest` (Friday "what changed this week").
Where the digest looks back at the week, the morning brief looks forward at
the day: items due today, items already overdue, anything freshly extracted
overnight, fresh tax-loss opportunities, and documents that landed since
yesterday. The advisor reads this with their first coffee and walks into
the day knowing what's on their plate.

Architecture (mirrors :mod:`services.digest`):

* :func:`compute_morning_brief` — pure function over a ``user_id``; walks
  every owned collection, gathers today-focused signals, returns a
  :class:`MorningBriefPayload`. Deterministic, no LLM call.
* :func:`render_morning_brief_html` / :func:`render_morning_brief_text` —
  presentation layer; see :mod:`services.morning_brief_template`.
* :func:`send_morning_brief_for_user` — orchestrator: compute, render,
  send via :func:`services.email.send_email`.
* :func:`run_scheduled_morning_brief_tick` — APScheduler entry point.
  Hourly tick that checks every opt-in user's preferred ``hour`` against
  *now* in the scheduler's TZ; sends when they match (and the user hasn't
  already received one in the last 24h).

Signal taxonomy — why today-focused matters:

* ``due_date``-bearing action items are bucketed by due date (overdue /
  due today / due this week). Items with no parseable due date are NOT
  surfaced under "due" buckets — they only appear if freshly extracted
  in the last 24h, since otherwise they'd just be every undated item the
  advisor already saw yesterday.
* TLH candidates use the same threshold as the weekly digest. Repeated
  every day is fine — a $5k tax-loss opportunity is news every morning
  until the advisor actions it.
* New documents from the last 24h give the "Finn caught something
  overnight" feeling — e.g., a custodian export that finished indexing
  while the advisor was asleep.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── Payload shape ─────────────────────────────────────────────────────────


@dataclass
class CollectionMorningSignals:
    """The per-collection slice of a morning brief payload."""

    collection_id: str
    collection_name: str
    overdue_action_items: list[dict[str, Any]] = field(default_factory=list)
    due_today_action_items: list[dict[str, Any]] = field(default_factory=list)
    due_this_week_action_items: list[dict[str, Any]] = field(default_factory=list)
    fresh_action_items: list[dict[str, Any]] = field(default_factory=list)
    tlh_candidates: list[dict[str, Any]] = field(default_factory=list)
    new_documents: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.overdue_action_items
            or self.due_today_action_items
            or self.due_this_week_action_items
            or self.fresh_action_items
            or self.tlh_candidates
            or self.new_documents
        )


@dataclass
class MorningBriefPayload:
    """Everything the renderer needs to produce a morning-brief email."""

    user_id: str
    generated_at: str  # ISO 8601, UTC
    date_label: str    # e.g. "Friday, May 22, 2026"
    collections: list[CollectionMorningSignals] = field(default_factory=list)

    @property
    def total_overdue(self) -> int:
        return sum(len(c.overdue_action_items) for c in self.collections)

    @property
    def total_due_today(self) -> int:
        return sum(len(c.due_today_action_items) for c in self.collections)

    @property
    def total_due_this_week(self) -> int:
        return sum(len(c.due_this_week_action_items) for c in self.collections)

    @property
    def total_fresh(self) -> int:
        return sum(len(c.fresh_action_items) for c in self.collections)

    @property
    def total_tlh_dollars(self) -> float:
        return sum(
            sum(float(item.get("unrealized_loss") or 0) for item in c.tlh_candidates)
            for c in self.collections
        )

    @property
    def total_new_documents(self) -> int:
        return sum(len(c.new_documents) for c in self.collections)

    @property
    def has_anything(self) -> bool:
        return any(not c.is_empty for c in self.collections)


# ─── Computation ───────────────────────────────────────────────────────────

DUE_THIS_WEEK_HORIZON_DAYS = 7
FRESH_HOURS = 24
NEW_DOCUMENTS_HOURS = 24
# Same threshold as the weekly digest — see services.digest for the rationale.
TLH_MIN_LOSS_DOLLARS = 2500.0
# Cap per-collection lists in the email — the morning brief is a glance, not
# an exhaustive report. Advisors who want the full list run the slash command
# in chat.
MAX_NEW_DOCUMENTS_PER_COLLECTION = 5


def compute_morning_brief(
    user_id: str,
    *,
    now: Optional[datetime] = None,
    tlh_min_loss: float = TLH_MIN_LOSS_DOLLARS,
) -> MorningBriefPayload:
    """Walk every collection owned by ``user_id`` and gather morning-brief signals.

    Pure read — no writes, no side effects. Safe to call from a preview
    endpoint or the scheduled tick.
    """
    from services.app_database import app_db

    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    today = now.date()
    this_week_end = today + timedelta(days=DUE_THIS_WEEK_HORIZON_DAYS)
    fresh_cutoff = now - timedelta(hours=FRESH_HOURS)
    new_docs_cutoff = now - timedelta(hours=NEW_DOCUMENTS_HOURS)
    date_label = _format_date_label(today)

    collections = app_db.get_all_collections(owner_id=user_id)
    signals: list[CollectionMorningSignals] = []

    for coll in collections:
        collection_id = coll["id"]
        cs = CollectionMorningSignals(
            collection_id=collection_id,
            collection_name=coll.get("name") or collection_id,
        )

        # ── Action items: bucket by due_date, fall back to fresh ───────
        try:
            meeting_notes_store = _get_meeting_notes_store(collection_id)
            if meeting_notes_store is not None:
                open_items = meeting_notes_store.list_action_items(
                    collection_id, status="open"
                )
                for item in open_items:
                    due = _parse_date(item.get("due_date"))
                    extracted_at = _parse_iso(item.get("extracted_at"))
                    if due is not None:
                        if due < today:
                            cs.overdue_action_items.append(item)
                        elif due == today:
                            cs.due_today_action_items.append(item)
                        elif due <= this_week_end:
                            cs.due_this_week_action_items.append(item)
                        # else: due further out — not this week's problem
                    elif extracted_at is not None and extracted_at >= fresh_cutoff:
                        # No parseable due date, but extracted overnight —
                        # this is "Finn extracted action items from yesterday's
                        # meeting; here's what surfaced." Worth a glance.
                        cs.fresh_action_items.append(item)
        except Exception as e:
            logger.warning(
                "Morning brief: action-items lookup failed for collection %s: %s",
                collection_id, e,
            )

        # ── Tax-loss candidates ───────────────────────────────────────
        try:
            holdings_store = _get_holdings_store(collection_id)
            if holdings_store is not None:
                from services.financial import tlh as tlh_mod
                candidates = tlh_mod.scan_unrealized_losses(
                    holdings_store,
                    min_loss=tlh_min_loss,
                    taxable_only=True,
                )
                for c in candidates:
                    symbol = (getattr(c, "symbol", None) or "").strip()
                    if not symbol:
                        continue
                    cs.tlh_candidates.append({
                        "symbol": symbol,
                        "account": getattr(c, "account", None) or "",
                        "unrealized_loss": float(getattr(c, "unrealized_loss", 0) or 0),
                        "loss_pct": getattr(c, "loss_pct", None),
                        "holding_period": getattr(c, "holding_period", None) or "",
                    })
        except Exception as e:
            logger.warning(
                "Morning brief: TLH scan failed for collection %s: %s",
                collection_id, e,
            )

        # ── New documents in last 24h ─────────────────────────────────
        try:
            metadata_store = _get_metadata_store(collection_id)
            if metadata_store is not None:
                docs = metadata_store.list_documents()
                for doc in docs:
                    uploaded_at = _parse_iso(doc.get("upload_timestamp"))
                    if uploaded_at is None or uploaded_at < new_docs_cutoff:
                        continue
                    cs.new_documents.append({
                        "filename": doc.get("filename") or "(unnamed)",
                        "document_id": doc.get("document_id"),
                        "num_pages": doc.get("num_pages"),
                        "upload_timestamp": doc.get("upload_timestamp"),
                    })
                    if len(cs.new_documents) >= MAX_NEW_DOCUMENTS_PER_COLLECTION:
                        break
        except Exception as e:
            logger.warning(
                "Morning brief: documents lookup failed for collection %s: %s",
                collection_id, e,
            )

        signals.append(cs)

    return MorningBriefPayload(
        user_id=user_id,
        generated_at=now.isoformat(),
        date_label=date_label,
        collections=signals,
    )


# ─── Delivery orchestrator ──────────────────────────────────────────────────


def send_morning_brief_for_user(
    user_id: str,
    *,
    to: Optional[str] = None,
    is_preview: bool = False,
) -> dict[str, Any]:
    """Compute, render, and send the morning brief for one user.

    Mirrors :func:`services.digest.send_digest_for_user` so the two surfaces
    behave identically from the caller's perspective.
    """
    from services.app_database import app_db
    from services.email import send_email
    from services.morning_brief_template import (
        render_morning_brief_html, render_morning_brief_text,
    )

    prefs = app_db.get_morning_brief_preferences(user_id)
    recipient = to or prefs.get("email_override") or user_id
    if not recipient or "@" not in recipient:
        return {
            "ok": False,
            "error": (
                "No email address available — set an override in morning-brief "
                "preferences or sign in via Cloudflare Access."
            ),
        }

    payload = compute_morning_brief(user_id)
    html_body = render_morning_brief_html(payload, is_preview=is_preview)
    text_body = render_morning_brief_text(payload, is_preview=is_preview)

    subject_prefix = "[Preview] " if is_preview else ""
    if payload.has_anything:
        subject = f"{subject_prefix}Finn morning brief — {payload.date_label}"
    else:
        subject = f"{subject_prefix}Finn morning brief — quiet day"

    result = send_email(
        to=recipient,
        subject=subject,
        html=html_body,
        text=text_body,
    )
    if result.ok:
        return {"ok": True, "id": result.id, "recipient": recipient, "payload": payload}
    return {"ok": False, "error": result.error, "recipient": recipient}


# ─── Scheduler entry point ─────────────────────────────────────────────────


def run_scheduled_morning_brief_tick(now: Optional[datetime] = None) -> dict[str, int]:
    """Hourly tick — sends the morning brief to users whose preferred ``hour``
    matches now in the scheduler's TZ. Called by APScheduler.

    Idempotent: a ``last_sent_at`` sentinel inside each user's preferences
    ensures the brief fires at most once per day. (The weekly digest uses a
    1-hour idempotency window since it fires once a week; the morning brief
    uses 23 hours so the next day's tick still qualifies — a 24-hour window
    would race against the same hour tomorrow.)
    """
    from services.app_database import app_db
    from services.scheduler import DEFAULT_TIMEZONE

    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    except Exception:
        tz = timezone.utc

    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    now_local = now.astimezone(tz)
    weekday, hour = now_local.weekday(), now_local.hour

    sent = 0
    skipped = 0
    failed = 0

    for prefs in app_db.list_morning_brief_optins():
        user_id = prefs["user_id"]
        if int(prefs.get("hour", 7)) != hour:
            skipped += 1
            continue
        # Weekday gating: Mon=0 .. Sun=6. If weekdays_only, skip Sat (5) and Sun (6).
        if prefs.get("weekdays_only", True) and weekday >= 5:
            skipped += 1
            continue

        # Idempotency guard: don't send twice within ~a day.
        last_sent = prefs.get("last_sent_at")
        if last_sent:
            try:
                ls = datetime.fromisoformat(last_sent)
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=tz)
                if (now_local - ls).total_seconds() < 23 * 3600:
                    skipped += 1
                    continue
            except ValueError:
                pass

        try:
            result = send_morning_brief_for_user(user_id)
            if result.get("ok"):
                sent += 1
                app_db.set_morning_brief_preferences(user_id, {"last_sent_at": now_local.isoformat()})
            else:
                failed += 1
                logger.warning(
                    "Morning brief send failed for user %s: %s",
                    user_id, result.get("error"),
                )
        except Exception:
            failed += 1
            logger.exception("Morning brief send raised for user %s", user_id)

    if sent or failed:
        logger.info(
            "Morning brief tick: sent=%d skipped=%d failed=%d (now=%s weekday=%d hour=%d)",
            sent, skipped, failed, now_local.isoformat(), weekday, hour,
        )
    return {"sent": sent, "skipped": skipped, "failed": failed}


# ─── Helpers ────────────────────────────────────────────────────────────────


def _format_date_label(d: date) -> str:
    """e.g. 'Friday, May 22, 2026'. Cross-platform — strftime %-d works on
    POSIX, %#d on Windows."""
    import platform
    fmt = "%A, %b %#d, %Y" if platform.system() == "Windows" else "%A, %b %-d, %Y"
    return d.strftime(fmt)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> Optional[date]:
    """Parse a due_date string. Accepts ISO date / datetime; everything else
    (e.g. 'next meeting', 'end of month') returns None so the item falls
    through to the fresh-or-skip path."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Try as plain ISO date first (cheap, common case).
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    # Fall back to datetime, which handles "2026-05-22T16:00:00" etc.
    dt = _parse_iso(text)
    return dt.date() if dt else None


def _get_meeting_notes_store(collection_id: str):
    """Return the MeetingNotesStore for a collection, or None if unavailable."""
    try:
        from services.indexer_manager import indexer_manager
        indexer = indexer_manager.get_indexer(collection_id)
        store = getattr(indexer.vector_store, "meeting_notes_store", None)
        return store
    except Exception:
        return None


def _get_holdings_store(collection_id: str):
    """Return the HoldingsStore for a collection, or None if unavailable."""
    try:
        from services.indexer_manager import indexer_manager
        indexer = indexer_manager.get_indexer(collection_id)
        store = getattr(indexer.vector_store, "holdings_store", None)
        return store
    except Exception:
        return None


def _get_metadata_store(collection_id: str):
    """Return the MetadataStore for a collection, or None if unavailable."""
    try:
        from services.indexer_manager import indexer_manager
        indexer = indexer_manager.get_indexer(collection_id)
        store = getattr(indexer.vector_store, "metadata_store", None)
        return store
    except Exception:
        return None
