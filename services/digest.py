"""Weekly advisor digest — *what changed this week* email.

Inserts Finn into the advisor's existing workflow: every week, a single
email summarising what changed across all their collections — new and
aging open action items, tax-loss opportunities, and (in v2) IPS-drift
flags and corporate events on held names.

Why an email and not an in-app card: advisors live in Outlook, not
dashboards. The "Finn caught something I'd have missed" moment has to
arrive *to them* — surfacing it inside an app they'd have to remember to
open buries the value.

Architecture:

* :func:`compute_weekly_digest` — pure function over a user_id; walks all
  the user's collections, gathers signals, returns a :class:`DigestPayload`.
  Deterministic, no LLM call.
* :func:`render_digest_html` / :func:`render_digest_text` — turn the
  payload into HTML + plain-text bodies (in services.digest_template).
* :func:`send_digest_for_user` — orchestrator: compute, render, send via
  ``services.email.send_email``. Returns the EmailResult so endpoints can
  surface failures to the caller (preview path) while the cron tick
  swallows them (logs only).
* :func:`run_scheduled_digest_tick` — APScheduler entry point. Hourly
  tick that checks every opt-in user's preferred (weekday, hour) against
  *now* in the scheduler's TZ; sends when they match.

The hourly tick keeps the cron model simple (one job, not one-per-user)
and idempotent — a run that fires twice in the same hour sends each
matched user once via a ``last_sent_at`` sentinel stored alongside their
preferences.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── Payload shape ─────────────────────────────────────────────────────────


@dataclass
class CollectionSignals:
    """The per-collection slice of a digest payload."""

    collection_id: str
    collection_name: str
    new_action_items: list[dict[str, Any]] = field(default_factory=list)
    aging_action_items: list[dict[str, Any]] = field(default_factory=list)
    tlh_candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.new_action_items or self.aging_action_items or self.tlh_candidates)


@dataclass
class DigestPayload:
    """Everything the renderer needs to produce a digest email."""

    user_id: str
    generated_at: str  # ISO 8601, UTC
    period_label: str  # e.g. "May 12 – May 19, 2026"
    collections: list[CollectionSignals] = field(default_factory=list)

    @property
    def total_new_action_items(self) -> int:
        return sum(len(c.new_action_items) for c in self.collections)

    @property
    def total_aging_action_items(self) -> int:
        return sum(len(c.aging_action_items) for c in self.collections)

    @property
    def total_tlh_dollars(self) -> float:
        return sum(
            sum(float(item.get("unrealized_loss") or 0) for item in c.tlh_candidates)
            for c in self.collections
        )

    @property
    def has_anything(self) -> bool:
        return any(not c.is_empty for c in self.collections)


# ─── Computation ───────────────────────────────────────────────────────────

AGING_ACTION_ITEM_DAYS = 30
# Higher than tlh.DEFAULT_MIN_LOSS ($500) because the digest is an
# at-a-glance "did anything notable happen this week" surface — long tails
# of $500 lots drown out the real opportunities. Advisors still get the
# full list via the /tlh slash command in chat.
TLH_MIN_LOSS_DOLLARS = 2500.0


def compute_weekly_digest(
    user_id: str,
    *,
    now: Optional[datetime] = None,
    aging_threshold_days: int = AGING_ACTION_ITEM_DAYS,
    tlh_min_loss: float = TLH_MIN_LOSS_DOLLARS,
) -> DigestPayload:
    """Walk every collection owned by ``user_id`` and gather digest signals.

    Pure read — no writes, no side effects. Safe to call from a preview
    endpoint or the scheduled tick.
    """
    from services.app_database import app_db
    from services.indexer_manager import indexer_manager

    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    week_ago = now - timedelta(days=7)
    aging_cutoff = now - timedelta(days=aging_threshold_days)
    period_label = f"{week_ago.strftime('%b %-d')} – {now.strftime('%b %-d, %Y')}" if not _is_windows() else f"{week_ago.strftime('%b %#d')} – {now.strftime('%b %#d, %Y')}"

    collections = app_db.get_all_collections(owner_id=user_id)
    signals: list[CollectionSignals] = []

    for coll in collections:
        collection_id = coll["id"]
        cs = CollectionSignals(
            collection_id=collection_id,
            collection_name=coll.get("name") or collection_id,
        )

        # ── Action items: new this week + aging > threshold ────────────
        try:
            meeting_notes_store = _get_meeting_notes_store(collection_id)
            if meeting_notes_store is not None:
                open_items = meeting_notes_store.list_action_items(
                    collection_id, status="open"
                )
                for item in open_items:
                    extracted_at = _parse_iso(item.get("extracted_at"))
                    if extracted_at is None:
                        continue
                    if extracted_at >= week_ago:
                        cs.new_action_items.append(item)
                    elif extracted_at < aging_cutoff:
                        cs.aging_action_items.append(item)
        except Exception as e:
            logger.warning(
                "Digest: action-items lookup failed for collection %s: %s",
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
                # Each candidate is a dataclass; convert to dict and keep
                # only the fields the email template needs. Skip rows
                # without a symbol — they're real lots in the holdings
                # table but an advisor can't act on "sell something
                # unidentified", so they're noise in a digest. The full
                # /tlh slash command still surfaces them so nothing is
                # silently hidden.
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
                "Digest: TLH scan failed for collection %s: %s",
                collection_id, e,
            )

        signals.append(cs)

    return DigestPayload(
        user_id=user_id,
        generated_at=now.isoformat(),
        period_label=period_label,
        collections=signals,
    )


# ─── Delivery orchestrator ──────────────────────────────────────────────────


def send_digest_for_user(
    user_id: str,
    *,
    to: Optional[str] = None,
    is_preview: bool = False,
) -> dict[str, Any]:
    """Compute, render, and send the weekly digest for one user.

    ``to`` overrides the destination address; otherwise we use the user's
    preference ``email_override`` if set, else the user_id (which is the
    verified email in hosted multi-user mode).
    """
    from services.app_database import app_db
    from services.email import send_email
    from services.digest_template import render_digest_html, render_digest_text

    prefs = app_db.get_digest_preferences(user_id)
    recipient = to or prefs.get("email_override") or user_id
    if not recipient or "@" not in recipient:
        return {
            "ok": False,
            "error": (
                "No email address available — set an override in digest "
                "preferences or sign in via Cloudflare Access."
            ),
        }

    payload = compute_weekly_digest(user_id)
    html_body = render_digest_html(payload, is_preview=is_preview)
    text_body = render_digest_text(payload, is_preview=is_preview)

    subject_prefix = "[Preview] " if is_preview else ""
    if payload.has_anything:
        subject = f"{subject_prefix}Finn weekly digest — {payload.period_label}"
    else:
        subject = f"{subject_prefix}Finn weekly digest — nothing new this week"

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


def run_scheduled_digest_tick(now: Optional[datetime] = None) -> dict[str, int]:
    """Hourly tick — sends digests to users whose scheduled (weekday, hour)
    matches now in the scheduler's TZ. Called by APScheduler.

    Idempotent: a ``last_sent_at`` sentinel inside each user's preferences
    ensures a duplicate tick within the same hour doesn't re-send.
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

    for prefs in app_db.list_digest_optins():
        user_id = prefs["user_id"]
        if int(prefs.get("weekday", 4)) != weekday or int(prefs.get("hour", 16)) != hour:
            skipped += 1
            continue

        # Idempotency guard: same calendar hour → skip
        last_sent = prefs.get("last_sent_at")
        if last_sent:
            try:
                ls = datetime.fromisoformat(last_sent)
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=tz)
                if (now_local - ls).total_seconds() < 3600:
                    skipped += 1
                    continue
            except ValueError:
                pass

        try:
            result = send_digest_for_user(user_id)
            if result.get("ok"):
                sent += 1
                app_db.set_digest_preferences(user_id, {"last_sent_at": now_local.isoformat()})
            else:
                failed += 1
                logger.warning(
                    "Digest send failed for user %s: %s",
                    user_id, result.get("error"),
                )
        except Exception:
            failed += 1
            logger.exception("Digest send raised for user %s", user_id)

    if sent or failed:
        logger.info(
            "Digest tick: sent=%d skipped=%d failed=%d (now=%s weekday=%d hour=%d)",
            sent, skipped, failed, now_local.isoformat(), weekday, hour,
        )
    return {"sent": sent, "skipped": skipped, "failed": failed}


# ─── Helpers ────────────────────────────────────────────────────────────────


def _is_windows() -> bool:
    import platform
    return platform.system() == "Windows"


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
