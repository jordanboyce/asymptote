"""Admit invited people at the Cloudflare Access edge.

Sharing a collection by email delivers a share token
(`services/share_email.py`), but the recipient still has to get past
Cloudflare Access before the app ever sees that token. Without this module an
invitation is only half-automated: the person clicks the emailed link, hits
the Access login, and is refused until someone adds them in the Cloudflare
dashboard by hand.

This closes that gap by editing the reusable Access policy that guards the
deployment — adding the invited address on invite, removing it when that
person's last share goes away.

Deliberately narrow, because an admission is a privileged act:

  - Admission grants edge access to the *whole deployment*, not to the one
    shared collection. The in-app private-collections layer is what confines
    the person to what was actually shared. So only `ADMIN_EMAILS` may
    trigger one; a non-admin owner can still share, but their invitee must
    already be able to reach the app.
  - Read-modify-write against the policy's `include` list, preserving every
    rule we did not put there (IdP groups, domain rules, service tokens).
  - Adds are idempotent; removes only ever touch an exact email rule.
  - Cloudflare caps a rule at 1,000 email addresses. We refuse at the cap
    rather than silently dropping someone.
  - OFFLINE_MODE disables it, like every outbound integration.
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

_API = "https://api.cloudflare.com/client/v4"

# Cloudflare account limit: "Email addresses per rule: 1,000".
MAX_EMAILS_PER_RULE = 1000

# Every edit of the policy is a read-modify-write of its whole include list;
# two concurrent edits would silently drop each other's rule. One process,
# so one lock fixes it.
_policy_write_lock = threading.Lock()


def access_provisioning_enabled() -> bool:
    """True when we hold everything needed to edit the Access policy."""
    return bool(
        settings.cf_api_token
        and settings.cf_account_id
        and settings.cf_access_policy_id
        and not settings.offline_mode
    )


def admin_emails() -> List[str]:
    return [e.strip().lower() for e in (settings.admin_emails or "").split(",") if e.strip()]


def is_admin(user_id: Optional[str]) -> bool:
    """Only these identities may admit someone at the edge.

    With no ADMIN_EMAILS configured nobody is an admin — provisioning stays
    off rather than defaulting open.
    """
    if not user_id:
        return False
    return user_id.strip().lower() in admin_emails()


def _cf(method: str, path: str, body: Optional[dict] = None) -> dict:
    req = urllib.request.Request(
        _API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {settings.cf_api_token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            errors = json.loads(e.read().decode("utf-8")).get("errors", [])
            detail = "; ".join(str(err.get("message", "")) for err in errors)
        except Exception:
            detail = ""
        raise RuntimeError(
            f"Cloudflare rejected {method} {path} ({e.code}): {detail or e.reason}. "
            f"Check CF_API_TOKEN has Account / Access: Apps and Policies / Edit."
        ) from e
    except Exception as e:
        raise RuntimeError(f"Could not reach the Cloudflare API: {e}") from e

    if not data.get("success", False):
        raise RuntimeError(f"Cloudflare rejected {method} {path}: {data.get('errors')}")
    return data.get("result") or {}


def _require_enabled():
    if not access_provisioning_enabled():
        raise RuntimeError(
            "Edge admission is not configured: set CF_API_TOKEN, CF_ACCOUNT_ID "
            "and CF_ACCESS_POLICY_ID in .env (scripts/provision_cloudflare.py "
            "writes all three)."
        )


def _policy_path() -> str:
    return f"/accounts/{settings.cf_account_id}/access/policies/{settings.cf_access_policy_id}"


def _get_policy() -> dict:
    return _cf("GET", _policy_path())


def _put_policy(policy: dict, include: List[dict]) -> None:
    """Write back the include list, echoing the fields Cloudflare requires.

    PUT replaces the policy, so we resend name/decision and the other rule
    lists unchanged rather than letting them be dropped.
    """
    body = {
        "name": policy.get("name") or "asymptote-invited",
        "decision": policy.get("decision") or "allow",
        "include": include,
    }
    for optional in ("exclude", "require", "session_duration"):
        if policy.get(optional):
            body[optional] = policy[optional]
    _cf("PUT", _policy_path(), body)


def _email_of(rule: dict) -> Optional[str]:
    """The address in an email rule, or None for any other kind of rule."""
    value = rule.get("email")
    if isinstance(value, dict) and value.get("email"):
        return str(value["email"]).strip().lower()
    return None


def admitted_emails() -> List[str]:
    """Every address currently admitted by an email rule on the policy."""
    _require_enabled()
    include = _get_policy().get("include") or []
    return [e for e in (_email_of(r) for r in include) if e]


def admit_email(email: str) -> bool:
    """Add an address to the Access policy. True if newly added.

    Idempotent: an address already present is left alone and returns False.
    """
    results = admit_emails([email])
    status = next(iter(results.values()), "rejected: no email address to admit")
    if status.startswith("rejected:"):
        raise RuntimeError(status.partition(":")[2].strip())
    return status == "added"


def admit_emails(emails: List[str]) -> Dict[str, str]:
    """Add many addresses in ONE read-modify-write of the Access policy.

    The per-address loop this replaces made N GET+PUT round-trips with a
    lost-update race between them; a roster invite would have been both slow
    and unsafe. Returns {email: "added" | "already" | "rejected: <why>"} —
    partial success is deliberate: addresses under the cap are admitted even
    when later ones are refused, and the caller reports per address.
    """
    _require_enabled()

    results: Dict[str, str] = {}
    targets: List[str] = []
    seen = set()
    for raw in emails:
        target = (raw or "").strip().lower()
        if not target:
            continue
        if "@" not in target:
            results[target or raw] = "rejected: not an email address"
            continue
        if target not in seen:
            seen.add(target)
            targets.append(target)
    if not targets and not results:
        raise RuntimeError("No email address to admit.")

    with _policy_write_lock:
        policy = _get_policy()
        include = list(policy.get("include") or [])
        present = {e for e in (_email_of(r) for r in include) if e}
        email_rule_count = len(present)

        added_any = False
        for target in targets:
            if target in present:
                results[target] = "already"
                continue
            if email_rule_count >= MAX_EMAILS_PER_RULE:
                results[target] = (
                    f"rejected: the Access policy already holds {MAX_EMAILS_PER_RULE} "
                    "email addresses (Cloudflare's per-rule cap). Remove some, or "
                    "move to an email-domain or IdP-group rule."
                )
                continue
            include.append({"email": {"email": target}})
            present.add(target)
            email_rule_count += 1
            results[target] = "added"
            added_any = True

        if added_any:
            _put_policy(policy, include)
            added = [e for e, r in results.items() if r == "added"]
            logger.info(
                f"Edge admission: added {len(added)} address(es) to Access policy "
                f"{settings.cf_access_policy_id}: {', '.join(added)}"
            )
    return results


def revoke_email(email: str) -> bool:
    """Remove an address from the Access policy. True if it was there.

    Only removes an exact email rule; every other rule on the policy — and
    the last remaining email rule, which would leave the policy invalid — is
    left untouched.
    """
    _require_enabled()
    target = (email or "").strip().lower()
    if not target:
        return False

    with _policy_write_lock:
        policy = _get_policy()
        include = list(policy.get("include") or [])
        remaining = [rule for rule in include if _email_of(rule) != target]

        if len(remaining) == len(include):
            return False
        if not remaining:
            # Access requires at least one Include rule; emptying it would lock
            # everyone out, including whoever is trying to fix it.
            raise RuntimeError(
                f"Refusing to remove {target}: it is the only Include rule on the "
                f"Access policy, and a policy with none locks the deployment out."
            )

        _put_policy(policy, remaining)
    logger.info(f"Edge admission: removed {target} from Access policy {settings.cf_access_policy_id}")
    return True
