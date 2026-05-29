"""Embedded APScheduler runtime for periodic background jobs.

Started from the FastAPI lifespan in :mod:`main`. Currently runs the weekly
digest and daily morning brief ticks; additional periodic work (auto-draft
Note of Record polling, calendar sync, etc.) will land here.

Design notes:

* ``BackgroundScheduler`` runs jobs in a background thread pool, decoupled
  from the FastAPI event loop. Jobs themselves are sync — they touch the
  app DB and call ``services.email.send_email`` (httpx, sync). That's the
  shape ``services.feedback`` already uses for transactional mail.

* Both the digest and morning-brief ticks fire hourly at ``minute=0``.
  Inside each tick, every opted-in user's preferred hour (and weekday, for
  the digest) is checked against the current value in the scheduler's
  configured timezone. Cheap when there's no match (only a few DB reads);
  does the real send only on the matching tick. Avoids the complexity of
  registering / unregistering per-user jobs every time a preference
  changes.

* The scheduler's timezone defaults to America/New_York, since the first
  pilot advisor is US-Eastern. Per-user timezones are a v2 add — when we
  get there, each user's preference will carry an IANA TZ string and the
  tick will compare against ``now_in(user.tz)``.
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "America/New_York"

_scheduler: Optional[BackgroundScheduler] = None


def start() -> None:
    """Start the embedded scheduler. Idempotent — safe to call twice."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(timezone=DEFAULT_TIMEZONE)

    # Import inside the registration so each tick module can import
    # scheduler-adjacent things without circular dependency at import time.
    from services.digest import run_scheduled_digest_tick
    from services.morning_brief import run_scheduled_morning_brief_tick

    _scheduler.add_job(
        run_scheduled_digest_tick,
        CronTrigger(minute=0),
        id="digest_hourly_tick",
        replace_existing=True,
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )

    _scheduler.add_job(
        run_scheduled_morning_brief_tick,
        CronTrigger(minute=0),
        id="morning_brief_hourly_tick",
        replace_existing=True,
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started (tz=%s, digest_hourly_tick + morning_brief_hourly_tick @ :00)",
        DEFAULT_TIMEZONE,
    )


def shutdown() -> None:
    """Stop the scheduler. Safe to call when not running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None


def get_scheduler() -> Optional[BackgroundScheduler]:
    return _scheduler
