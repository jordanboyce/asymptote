"""Resend-backed transactional email sender.

A thin wrapper over Resend's REST endpoint so callers don't carry the HTTP
shape, retry posture, or settings lookup. Used by services.feedback (issue
reports) and services.digest (weekly advisor digest).

The Resend API key and default From address live in ``settings.resend_api_key``
and ``settings.feedback_email_from`` — same config the feedback feature has
shipped with since v4.4.3 R10. Leaving the API key empty disables outbound
mail; callers get ``EmailResult(ok=False, error=...)`` and decide whether to
surface that to the user or log silently.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
DEFAULT_FROM = "Finn <onboarding@resend.dev>"
# Resend usually responds in <2s; the long tail can stretch past 15s. The
# Friday digest cron runs unattended, so a single slow request shouldn't
# fail the whole tick — we give it more headroom and retry once on
# timeout / network errors (NOT on 4xx, which we want to surface verbatim).
DEFAULT_TIMEOUT_SECONDS = 30.0
RETRY_ATTEMPTS = 2


@dataclass
class EmailResult:
    ok: bool
    id: Optional[str] = None
    error: Optional[str] = None


def send_email(
    *,
    to: str | list[str],
    subject: str,
    html: str,
    text: Optional[str] = None,
    from_addr: Optional[str] = None,
    reply_to: Optional[str] = None,
    tags: Optional[list[dict[str, str]]] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> EmailResult:
    """Send one email via Resend.

    Returns ``EmailResult(ok=True, id=...)`` on success, or
    ``EmailResult(ok=False, error=...)`` on any failure — caller decides
    whether the failure is user-visible (preview endpoints) or silent
    (background cron job, which logs and moves on).
    """
    if not settings.resend_api_key:
        return EmailResult(ok=False, error="Email delivery not configured (RESEND_API_KEY missing).")

    recipients = [to] if isinstance(to, str) else list(to)
    if not recipients:
        return EmailResult(ok=False, error="No recipients provided.")

    sender = from_addr or settings.feedback_email_from or DEFAULT_FROM

    payload: dict[str, Any] = {
        "from": sender,
        "to": recipients,
        "subject": subject,
        "html": html,
    }
    if text is not None:
        payload["text"] = text
    if reply_to:
        payload["reply_to"] = reply_to
    if tags:
        payload["tags"] = tags

    # Stable per-call idempotency key. Resend dedupes within ~24h on this
    # header, so if the first POST succeeded server-side but the response
    # never arrived (slow network, client timeout), the retry returns the
    # original message id instead of sending a duplicate. Without this,
    # retries on transient timeouts would double-deliver — exactly the
    # failure mode that triggered this code path.
    idempotency_key = f"finn-{uuid.uuid4()}"

    last_transient_error: Optional[str] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    RESEND_ENDPOINT,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {settings.resend_api_key}",
                        "Content-Type": "application/json",
                        "Idempotency-Key": idempotency_key,
                    },
                )
            if resp.status_code >= 400:
                logger.error(
                    "Resend rejected email (status=%s, from=%s, to=%s): %s",
                    resp.status_code, sender, recipients, resp.text[:500],
                )
                # Surface Resend's reason in the error so the caller can
                # debug without digging through logs — most 403s are
                # misconfigured sender domain or testing-mode recipient
                # restriction. 4xx is NOT retried; retry would just 4xx
                # again and add latency to a known-bad call.
                reason = ""
                try:
                    body = resp.json() if resp.content else {}
                    msg = body.get("message") or body.get("error") or ""
                    if msg:
                        reason = f" — {str(msg)[:240]}"
                except Exception:
                    pass
                return EmailResult(
                    ok=False,
                    error=f"Email delivery failed (HTTP {resp.status_code}){reason}",
                )
            data = resp.json() if resp.content else {}
            return EmailResult(ok=True, id=data.get("id"))
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            # Transient — retry once before giving up.
            last_transient_error = f"{type(e).__name__}: {e}"
            logger.warning(
                "Email send transient failure (attempt %d/%d): %s",
                attempt, RETRY_ATTEMPTS, last_transient_error,
            )
            continue
        except Exception as e:
            logger.exception("Email delivery failed")
            return EmailResult(ok=False, error=f"Email delivery failed: {e}")

    return EmailResult(
        ok=False,
        error=f"Email delivery timed out after {RETRY_ATTEMPTS} attempts ({last_transient_error}).",
    )
