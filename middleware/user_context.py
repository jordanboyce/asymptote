"""User context extraction for multi-user isolation.

In multi-user mode, the user identity comes from the X-User-ID header,
typically set by a company's auth proxy (e.g., OAuth2 proxy, Traefik forward auth).

In single-user mode, all requests use the configured default_user_id.
"""

import logging

from fastapi import Request

from config import settings
from services.app_database import app_db

logger = logging.getLogger(__name__)


def get_current_user_id(request: Request) -> str:
    """Extract the current user ID from the request.

    In single-user mode: always returns settings.default_user_id.
    In multi-user mode: reads X-User-ID header, falls back to default.

    Also auto-provisions the user record on first sight.
    """
    if not settings.enable_multi_user:
        return settings.default_user_id

    user_id = request.headers.get("X-User-ID", "").strip()
    if not user_id:
        return settings.default_user_id

    # Auto-provision user on first sight
    display_name = request.headers.get("X-User-Name", "").strip() or None
    try:
        app_db.upsert_user(user_id, display_name=display_name)
    except Exception as e:
        logger.warning(f"Could not upsert user {user_id}: {e}")

    return user_id
