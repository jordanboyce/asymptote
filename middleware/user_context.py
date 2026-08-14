"""User context extraction for multi-user isolation.

Single-user mode (the default) is unchanged: every request is
``settings.default_user_id`` and nothing here does any work.

Multi-user mode takes identity from an authenticating proxy in front of the
app, and there are two ways to do that — one safe, one conditionally safe:

1. **Cloudflare Access (preferred).** Set ``access_team_domain`` and
   ``access_aud``. Identity comes from the *verified signature* on the
   ``Cf-Access-Jwt-Assertion`` token. Forging it requires Cloudflare's signing
   key, so this holds even if the origin is reachable directly.

2. **A plain trusted-header proxy.** Set ``trust_proxy_user_header=true`` and
   the app will believe ``X-User-ID`` / ``Cf-Access-Authenticated-User-Email``.
   This is only as strong as the guarantee that nothing can reach the origin
   except through the proxy, because a plaintext header is trivially forged.

With neither configured, multi-user mode **refuses the request** rather than
falling through to the default user.

> That fallthrough is what this module used to do: an unauthenticated request
> in multi-user mode silently became ``default_user_id`` — the account that
> owns everything on a machine upgraded from single-user mode. The bypass and
> the jackpot were the same account. A missing identity is now a 401.
"""

import logging

from fastapi import HTTPException, Request, status

from config import settings
from services.app_database import app_db

logger = logging.getLogger(__name__)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
)


def get_current_user_id(request: Request) -> str:
    """Resolve the calling user, or raise 401.

    In single-user mode: always ``settings.default_user_id``, no checks.
    In multi-user mode: a verified Access identity, or a trusted proxy header
    when explicitly enabled. Never an implicit fallback.
    """
    if not settings.enable_multi_user:
        return settings.default_user_id

    user_id, display_name = _identify(request)
    _provision(user_id, display_name)
    return user_id


def _identify(request: Request) -> tuple[str, str | None]:
    from services.access_auth import (
        EMAIL_HEADER,
        JWT_HEADER,
        AccessAuthError,
        AccessConfigError,
        get_verifier,
    )

    try:
        verifier = get_verifier()
    except AccessConfigError as exc:
        # Half-configured Access is an operator error, and guessing which half
        # was meant is how a deployment ends up unauthenticated by accident.
        logger.error("Cloudflare Access is misconfigured: %s", exc)
        raise _UNAUTHENTICATED from exc

    if verifier is not None:
        token = request.headers.get(JWT_HEADER, "").strip()
        if not token:
            logger.warning(
                "Multi-user request with no %s header — rejecting. If this is "
                "unexpected, the request likely reached the origin without "
                "passing through Cloudflare Access.",
                JWT_HEADER,
            )
            raise _UNAUTHENTICATED
        try:
            return verifier.identity_from(token)
        except AccessAuthError as exc:
            raise _UNAUTHENTICATED from exc

    if settings.trust_proxy_user_header:
        email = request.headers.get(EMAIL_HEADER, "").strip()
        user_id = email or request.headers.get("X-User-ID", "").strip()
        if not user_id:
            raise _UNAUTHENTICATED
        display_name = request.headers.get("X-User-Name", "").strip() or email or None
        return user_id.lower(), display_name

    logger.error(
        "enable_multi_user is on but no identity source is configured. Set "
        "access_team_domain + access_aud (preferred), or set "
        "trust_proxy_user_header=true if a trusted proxy supplies X-User-ID."
    )
    raise _UNAUTHENTICATED


def _provision(user_id: str, display_name: str | None) -> None:
    """Auto-provision the user record on first sight.

    Best-effort: a provisioning failure must not deny an already-authenticated
    user, so it logs and moves on.
    """
    try:
        app_db.upsert_user(user_id, display_name=display_name)
    except Exception as e:
        logger.warning("Could not upsert user %s: %s", user_id, e)
