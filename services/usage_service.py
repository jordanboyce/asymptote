"""Per-user chat usage accounting and the daily token budget.

The deployment's default posture is a shared, free-tier LLM key. That is
only safe with many users if two things hold: every turn's spend is
recorded against an identity, and a runaway user hits a ceiling before the
provider throttles the whole team. This module owns both.

Recording must never fail a chat request — a lost usage row is a rounding
error, a 500 on a successful answer is not. The budget check, by contrast,
fails open on database errors: an unreadable usage table shouldn't lock
everyone out of chat.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import settings
from services.app_database import app_db

logger = logging.getLogger(__name__)


def _utc_midnight_iso() -> str:
    """Start of the current UTC day — the budget window boundary."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None).isoformat()


def _seconds_to_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    return max(1, int(86400 - (now.hour * 3600 + now.minute * 60 + now.second)))


def record_chat_usage(
    user_id: Optional[str],
    collection_id: str,
    provider: str,
    model: Optional[str],
    input_tokens: int,
    output_tokens: int,
    tool_calls: int = 0,
    cache_hit: bool = False,
    duration_ms: Optional[int] = None,
) -> None:
    """Persist one chat turn's spend. Swallows every error deliberately."""
    try:
        app_db.add_chat_usage(
            user_id=user_id,
            collection_id=collection_id,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
            cache_hit=cache_hit,
            duration_ms=duration_ms,
        )
    except Exception as e:
        logger.warning(f"Failed to record chat usage: {e}")


def check_daily_budget(user_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """None when the user may chat; otherwise a dict describing the 429.

    The budget counts input+output tokens since UTC midnight against
    CHAT_DAILY_TOKEN_BUDGET (0 = unlimited). Anonymous password callers all
    share the None-identity row — password mode is a trusted team, and a
    shared ceiling still protects the key.
    """
    budget = settings.chat_daily_token_budget
    if not budget:
        return None
    try:
        used = app_db.get_user_usage_since(user_id, _utc_midnight_iso())
    except Exception as e:
        logger.warning(f"Budget check failed open: {e}")
        return None
    spent = int(used.get("input_tokens", 0)) + int(used.get("output_tokens", 0))
    if spent < budget:
        return None
    retry_after = _seconds_to_utc_midnight()
    return {
        "detail": (
            f"Daily chat token budget reached ({spent:,} of {budget:,} tokens "
            "used today). The budget resets at midnight UTC. Cached answers "
            "and search remain available."
        ),
        "retry_after_seconds": retry_after,
    }
