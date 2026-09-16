"""Online registration: let people ask for access from a public page.

Admission today is admin-push: someone shares a collection or types an
address into Admin → Access, and the app adds that address to the Cloudflare
Access policy (``services/access_provisioning.py``). This module adds the
pull side. A person fills in ``/register`` — a page Cloudflare Access is told
to bypass — and either

- ``REGISTRATION_MODE=open``: they are admitted immediately (subject to the
  domain allowlist and the seat cap), or
- ``REGISTRATION_MODE=approval``: the request queues for an admin, who
  approves or denies it from Admin → Access → Requests.

Either way the person still signs in through Cloudflare Access (one-time
PIN or the team's IdP); nothing here mints a session. The app never learns
a password, and an approved address is exactly as admitted as one an admin
typed by hand — it is the same policy edit.

What is deliberately refused:

- Registration without edge admission configured. Approving a request *is*
  an admission; without ``CF_API_TOKEN`` etc. it could only pile up rows
  nobody can act on, so the page reports itself closed instead.
- Addresses outside ``REGISTRATION_ALLOWED_DOMAINS`` when that is set, in
  both modes — the queue is not a place to collect strangers' addresses.
- Anything past ``REGISTRATION_MAX_SEATS`` admitted addresses. In open mode
  the request queues for a human instead of auto-admitting; on approval the
  admin sees the seat count and decides.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from config import settings
from services import audit
from services.access_provisioning import (
    access_provisioning_enabled,
    admit_email,
    admitted_emails,
    admin_emails,
)

logger = logging.getLogger(__name__)

STATUSES = ("pending", "approved", "denied")

# Practical rather than RFC-complete: one @, no whitespace, a dotted domain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_FIELD = 200
_MAX_NOTE = 1000


class RegistrationError(Exception):
    """A request the caller can fix (400-class)."""

    status_code = 400


class RegistrationClosed(RegistrationError):
    status_code = 403


def registration_enabled() -> bool:
    """True when the page should accept requests at all."""
    return settings.registration_mode != "off" and access_provisioning_enabled()


def allowed_domains() -> List[str]:
    return [
        d.strip().lower().lstrip("@")
        for d in (settings.registration_allowed_domains or "").split(",")
        if d.strip()
    ]


def public_config() -> Dict[str, Any]:
    """What the public page needs, and nothing about the deployment it shouldn't have."""
    enabled = registration_enabled()
    return {
        "enabled": enabled,
        "mode": settings.registration_mode if enabled else "off",
        "allowed_domains": allowed_domains() if enabled else [],
        "product": "Asymptote",
    }


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if not email or len(email) > _MAX_FIELD or not _EMAIL_RE.match(email):
        raise RegistrationError("Enter a valid email address.")
    return email


def _clean(value: Optional[str], limit: int) -> str:
    text = (value or "").strip()
    # Collapse control characters; these fields are shown to admins verbatim.
    text = "".join(ch for ch in text if ch == "\n" or ch >= " ")
    return text[:limit]


def domain_allowed(email: str) -> bool:
    domains = allowed_domains()
    if not domains:
        return True
    domain = email.rsplit("@", 1)[-1]
    return any(domain == d or domain.endswith("." + d) for d in domains)


def seats() -> Dict[str, Any]:
    """Admitted addresses against the registration seat cap.

    Reads the live policy; a Cloudflare error surfaces as ``None`` counts so
    callers can degrade (queue rather than refuse) instead of guessing.
    """
    cap = max(0, int(settings.registration_max_seats or 0))
    try:
        admitted = admitted_emails()
    except Exception as e:
        logger.warning(f"Registration: could not read the Access policy: {e}")
        return {"admitted": None, "cap": cap, "available": None}
    available = (cap - len(admitted)) if cap else None
    return {"admitted": len(admitted), "cap": cap, "available": available, "emails": admitted}


def submit(
    email_raw: str,
    name: str = "",
    organization: str = "",
    note: str = "",
    request_ip: Optional[str] = None,
) -> Dict[str, Any]:
    """Handle one submission from the public page.

    Returns ``{"status": ..., "message": ...}`` where status is one of
    ``admitted`` (already on the policy — just sign in), ``approved`` (open
    mode admitted them now), or ``pending`` (queued for an admin).
    """
    if not registration_enabled():
        raise RegistrationClosed("Registration is closed on this deployment. Ask an administrator for access.")

    email = normalize_email(email_raw)
    name = _clean(name, _MAX_FIELD)
    organization = _clean(organization, _MAX_FIELD)
    note = _clean(note, _MAX_NOTE)

    if not domain_allowed(email):
        domains = ", ".join(allowed_domains())
        raise RegistrationError(
            f"Registration is limited to addresses at {domains}."
        )

    from services.app_database import app_db

    seat_info = seats()
    already = seat_info.get("emails") or []
    if email in already or email in admin_emails():
        audit.record("access.register", actor=None, target=email,
                     detail={"outcome": "already_admitted", "ip": request_ip})
        return {
            "status": "admitted",
            "email": email,
            "message": "This address already has access. Sign in to continue.",
        }

    existing = app_db.get_registration_request_by_email(email)
    if existing and existing.get("status") == "pending":
        app_db.upsert_registration_request(email, name, organization, note, request_ip)
        return {
            "status": "pending",
            "email": email,
            "message": "Your request is already waiting for review. You'll receive an email once it's approved.",
        }

    row = app_db.upsert_registration_request(email, name, organization, note, request_ip)

    seats_full = seat_info.get("available") is not None and seat_info["available"] <= 0
    if settings.registration_mode == "open" and not seats_full:
        try:
            admit_email(email)
        except Exception as e:
            # The edge said no (rate limit, token scope, rule cap): keep the
            # request so an admin can retry it rather than losing the person.
            logger.warning(f"Registration: auto-admission of {email} failed: {e}")
            audit.record("access.register", actor=None, target=email,
                         detail={"outcome": "queued_after_error", "error": str(e), "ip": request_ip})
            _notify_admins(row)
            return {
                "status": "pending",
                "email": email,
                "message": "We couldn't finish setting up your access automatically. An administrator will review your request.",
            }
        app_db.decide_registration_request(row["id"], "approved", "auto", "open registration")
        audit.record("access.register", actor=None, target=email,
                     detail={"outcome": "approved", "mode": "open", "ip": request_ip})
        logger.info(f"Registration: admitted {email} (open mode)")
        return {
            "status": "approved",
            "email": email,
            "message": "You're in. Sign in with this address to get started.",
        }

    outcome = "queued_seats_full" if seats_full else "queued"
    audit.record("access.register", actor=None, target=email,
                 detail={"outcome": outcome, "mode": settings.registration_mode, "ip": request_ip})
    _notify_admins(row)
    return {
        "status": "pending",
        "email": email,
        "message": "Thanks — your request has been sent for review. You'll receive an email once it's approved.",
    }


def _notify_admins(row: Dict[str, Any]) -> None:
    """Best-effort email to the admin list; silence when mail is not configured."""
    try:
        from services.share_email import share_email_enabled, send_plain_email
    except Exception:
        return
    if not share_email_enabled():
        return
    who = row.get("email", "")
    lines = [
        f"{who} asked for access to Asymptote.",
        "",
        f"Name: {row.get('name') or '—'}",
        f"Organization: {row.get('organization') or '—'}",
        f"Note: {row.get('note') or '—'}",
        "",
        "Review it under Admin → Access → Requests.",
    ]
    for admin in admin_emails():
        try:
            send_plain_email(admin, subject=f"Access request from {who}", text="\n".join(lines))
        except Exception as e:
            logger.warning(f"Registration: could not notify {admin}: {e}")


def list_requests(status: Optional[str] = None) -> List[Dict[str, Any]]:
    from services.app_database import app_db

    if status and status not in STATUSES:
        raise RegistrationError(f"status must be one of {', '.join(STATUSES)}")
    return app_db.list_registration_requests(status=status)


def approve(request_id: str, admin: Optional[str], note: str = "") -> Dict[str, Any]:
    """Admit the address at the edge and mark the request approved."""
    from services.app_database import app_db

    row = app_db.get_registration_request(request_id)
    if row is None:
        raise KeyError(request_id)
    if row.get("status") == "approved":
        return row
    admit_email(row["email"])  # raises RuntimeError with Cloudflare's reason
    updated = app_db.decide_registration_request(request_id, "approved", admin, note)
    audit.record("access.approve_registration", actor=admin, target=row["email"],
                 detail={"request_id": request_id, "note": note})
    _notify_applicant(row["email"], approved=True)
    logger.info(f"Registration: {admin} approved {row['email']}")
    return updated


def deny(request_id: str, admin: Optional[str], note: str = "") -> Dict[str, Any]:
    from services.app_database import app_db

    row = app_db.get_registration_request(request_id)
    if row is None:
        raise KeyError(request_id)
    updated = app_db.decide_registration_request(request_id, "denied", admin, note)
    audit.record("access.deny_registration", actor=admin, target=row["email"],
                 detail={"request_id": request_id, "note": note})
    logger.info(f"Registration: {admin} denied {row['email']}")
    return updated


def _notify_applicant(email: str, *, approved: bool) -> None:
    try:
        from services.share_email import share_email_enabled, send_plain_email
    except Exception:
        return
    if not share_email_enabled() or not approved:
        return
    try:
        send_plain_email(
            email,
            subject="Your Asymptote access is ready",
            text=(
                "Your request for access was approved. Open the workspace and sign in "
                "with this address; you'll receive a one-time code by email."
            ),
        )
    except Exception as e:
        logger.warning(f"Registration: could not notify {email}: {e}")
