"""Cloudflare Access application-token verification.

Cloudflare Access authenticates the user at the edge and forwards proof to the
origin two ways:

  * ``Cf-Access-Jwt-Assertion`` — a signed JWT. This is the proof.
  * ``Cf-Access-Authenticated-User-Email`` — a convenience header carrying the
    same email in plaintext. This is **not** proof of anything.

Cloudflare is explicit about the difference: "Validation of the header alone is
not sufficient — the JWT and signature must be confirmed to avoid identity
spoofing." A plaintext header is trivially forged by anyone who can reach the
origin directly, so trusting it means the origin's security rests entirely on
that path being unreachable. With a Tunnel that happens to be true — the origin
makes an outbound connection and listens on no public port — but "we are safe
because of a network property nobody re-checks" is exactly the assumption that
quietly stops holding after a config change. Verify the signature instead.

Keys come from the team's JWKS endpoint and rotate roughly every six weeks; the
endpoint publishes the current and previous key, so a cached set stays valid
across a rotation. We cache for an hour.

Docs: https://developers.cloudflare.com/cloudflare-one/identity/authorization-cookie/validating-json/
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Access rotates signing keys about every six weeks and serves the previous key
# alongside the current one, so an hour of staleness is harmless and keeps us
# off the network on every request.
_JWKS_TTL_SECONDS = 3600
_JWKS_TIMEOUT_SECONDS = 5.0

JWT_HEADER = "Cf-Access-Jwt-Assertion"
EMAIL_HEADER = "Cf-Access-Authenticated-User-Email"


class AccessAuthError(Exception):
    """The request carried no valid Cloudflare Access token."""


class AccessConfigError(Exception):
    """Access verification was requested but is not configured correctly."""


class AccessVerifier:
    """Verifies Cloudflare Access JWTs for one team domain + application.

    Thread-safe: the JWKS cache is guarded by a lock, since FastAPI serves
    requests from a thread pool and a cold cache would otherwise let several
    requests fetch the same key set at once.
    """

    def __init__(self, team_domain: str, audience: str) -> None:
        self.team_domain = _normalize_team_domain(team_domain)
        self.audience = (audience or "").strip()
        if not self.team_domain:
            raise AccessConfigError("Cloudflare Access team domain is not set")
        if not self.audience:
            raise AccessConfigError("Cloudflare Access AUD tag is not set")

        self.issuer = f"https://{self.team_domain}"
        self.certs_url = f"{self.issuer}/cdn-cgi/access/certs"

        self._lock = threading.Lock()
        self._jwks_client: Any = None
        self._jwks_fetched_at: float = 0.0

    # ── public API ────────────────────────────────────────────────────────

    def verify(self, token: str) -> dict[str, Any]:
        """Return the verified claims, or raise :class:`AccessAuthError`.

        Every failure mode collapses to AccessAuthError on purpose — the caller
        turns it into a 401 and must not be tempted to branch on the reason.
        The specifics go to the log, where an operator can see them and an
        attacker cannot.
        """
        if not token:
            raise AccessAuthError("no Access token on request")

        import jwt  # PyJWT — imported lazily so the module loads without it

        try:
            signing_key = self._signing_key_for(token)
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except AccessAuthError:
            raise
        except Exception as exc:  # PyJWT raises a family of these
            logger.warning("Access token rejected: %s: %s", type(exc).__name__, exc)
            raise AccessAuthError("Access token failed verification") from exc

        return claims

    def identity_from(self, token: str) -> tuple[str, str | None]:
        """Verify *token* and return ``(user_id, display_name)``.

        The email claim is the identity. Service tokens authenticate a machine
        rather than a person and carry no email — they are rejected here rather
        than silently mapped onto some user's data.
        """
        claims = self.verify(token)

        email = (claims.get("email") or "").strip().lower()
        if not email:
            common_name = claims.get("common_name")
            if common_name:
                raise AccessAuthError(
                    "service-token identity is not accepted for user routes"
                )
            raise AccessAuthError("Access token carries no email claim")

        return email, email

    # ── internals ─────────────────────────────────────────────────────────

    def _signing_key_for(self, token: str):
        client = self._get_jwks_client()
        try:
            return client.get_signing_key_from_jwt(token).key
        except Exception as exc:
            # A key ID we have never seen can simply mean the cache predates a
            # rotation. Refetch once before giving up.
            logger.info("Access signing key miss (%s) — refreshing JWKS", exc)
            client = self._get_jwks_client(force=True)
            try:
                return client.get_signing_key_from_jwt(token).key
            except Exception as exc2:
                logger.warning("Access signing key still unknown after refresh: %s", exc2)
                raise AccessAuthError("unknown Access signing key") from exc2

    def _get_jwks_client(self, force: bool = False):
        from jwt import PyJWKClient

        with self._lock:
            fresh = (time.monotonic() - self._jwks_fetched_at) < _JWKS_TTL_SECONDS
            if self._jwks_client is not None and fresh and not force:
                return self._jwks_client

            # PyJWKClient does its own internal caching; we rebuild it to force
            # a refetch and to bound staleness ourselves.
            self._jwks_client = PyJWKClient(
                self.certs_url,
                cache_keys=True,
                timeout=_JWKS_TIMEOUT_SECONDS,
            )
            self._jwks_fetched_at = time.monotonic()
            return self._jwks_client


def _normalize_team_domain(value: str) -> str:
    """Accept ``acme``, ``acme.cloudflareaccess.com``, or a full URL.

    Operators copy this value out of the dashboard in whatever shape it happens
    to appear, and a trailing slash or an ``https://`` prefix silently breaks
    issuer comparison — which surfaces as "every login is rejected" with no
    clue why.
    """
    domain = (value or "").strip()
    if not domain:
        return ""
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.strip("/")
    if "/" in domain:
        domain = domain.split("/", 1)[0]
    if not domain:
        return ""
    if "." not in domain:
        domain = f"{domain}.cloudflareaccess.com"
    return domain.lower()


# ── module-level accessor ────────────────────────────────────────────────

_verifier: AccessVerifier | None = None
_verifier_key: tuple[str, str] | None = None
_verifier_lock = threading.Lock()


def get_verifier() -> AccessVerifier | None:
    """Return the configured verifier, or None when Access is not configured.

    Rebuilt when the settings change so a test (or a settings edit) does not
    get a verifier pinned to stale configuration.
    """
    from config import settings

    team = (settings.access_team_domain or "").strip()
    aud = (settings.access_aud or "").strip()
    if not team or not aud:
        return None

    global _verifier, _verifier_key
    key = (team, aud)
    with _verifier_lock:
        if _verifier is None or _verifier_key != key:
            _verifier = AccessVerifier(team_domain=team, audience=aud)
            _verifier_key = key
        return _verifier


def reset_verifier_cache() -> None:
    """Drop the cached verifier. For tests and settings reloads."""
    global _verifier, _verifier_key
    with _verifier_lock:
        _verifier = None
        _verifier_key = None
