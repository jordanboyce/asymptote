"""User context extraction for multi-user isolation.

In multi-user mode, the user identity comes from a trusted auth proxy in
front of the app. Two headers are accepted, in priority order:

1. ``Cf-Access-Authenticated-User-Email`` — set by Cloudflare Access. The
   value is the user's verified email and we use it directly as the user_id.
2. ``X-User-ID`` — generic header for other proxies (OAuth2 proxy, Traefik
   forward auth, Authelia, etc.).

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
    In multi-user mode: prefers Cloudflare Access email, then X-User-ID,
    then falls back to the configured default.

    Also auto-provisions the user record on first sight.
    """
    if not settings.enable_multi_user:
        return settings.default_user_id

    cf_email = request.headers.get("Cf-Access-Authenticated-User-Email", "").strip()
    user_id = cf_email or request.headers.get("X-User-ID", "").strip()
    if not user_id:
        return settings.default_user_id

    # Auto-provision user on first sight. Cloudflare Access gives us a
    # verified email, so use it as the display name when no override is set.
    display_name = request.headers.get("X-User-Name", "").strip() or cf_email or None
    try:
        app_db.upsert_user(user_id, display_name=display_name)
    except Exception as e:
        logger.warning(f"Could not upsert user {user_id}: {e}")

    return user_id
