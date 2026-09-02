"""Operator-only visibility endpoints.

The Phase-2 admin console reads these: who is spending the shared key on
what, what the process is doing right now, and the search audit trail that
search_history finally became once it gained identity columns.

Every endpoint calls require_admin first. Under private collections that
means ADMIN_EMAILS only (fails closed when the list is empty); with private
collections off it is a deliberate no-op — the shared-appliance model
treats everyone who can reach the app as a trusted teammate, same as
POST /api/config.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from api.deps import require_admin
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/admin/usage", summary="Chat usage rollups", tags=["admin"])
async def get_usage(days: int = 30):
    """Per-user and per-day chat spend for the last `days` days."""
    require_admin("view usage reports")
    from services.app_database import app_db

    days = max(1, min(days, 365))
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        return {
            "days": days,
            "by_user": app_db.get_usage_summary(since, group_by="user"),
            "by_day": app_db.get_usage_summary(since, group_by="day"),
            "daily_token_budget": settings.chat_daily_token_budget,
        }
    except Exception as e:
        logger.error(f"Usage rollup failed: {e}")
        raise HTTPException(status_code=500, detail="Could not read usage data")


@router.get("/api/admin/search-history", summary="Recent searches", tags=["admin"])
async def get_search_history(limit: int = 100):
    """The search audit trail: who searched what, where, and when."""
    require_admin("view the search audit trail")
    from services.app_database import app_db

    limit = max(1, min(limit, 500))
    return {
        "retention_days": settings.search_history_retention_days,
        "searches": app_db.get_search_history(limit=limit),
    }


@router.get("/api/admin/stats", summary="Live process stats", tags=["admin"])
async def get_stats():
    """What the process is doing right now, plus today's spend."""
    require_admin("view system stats")
    from middleware.rate_limit import rate_limiter
    from middleware.request_meta import stats as request_stats
    from services.app_database import app_db
    from services.upload_service import upload_service

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    try:
        usage_today_rows = app_db.get_usage_summary(today, group_by="day")
        usage_today = usage_today_rows[0] if usage_today_rows else {
            "turns": 0, "input_tokens": 0, "output_tokens": 0,
            "tool_calls": 0, "cache_hits": 0,
        }
    except Exception:
        usage_today = None

    try:
        index_jobs_active = upload_service.active_job_count()
    except Exception:
        index_jobs_active = None

    return {
        **request_stats(),
        "rate_limit": {
            "enabled": settings.rate_limit_enabled,
            "classes": rate_limiter.stats(),
        },
        "index_jobs_active": index_jobs_active,
        "max_concurrent_index_jobs": settings.max_concurrent_index_jobs,
        "usage_today": usage_today,
        "daily_token_budget": settings.chat_daily_token_budget,
    }
