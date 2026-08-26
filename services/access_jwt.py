"""Cloudflare Access JWT verification.

When Asymptote runs behind Cloudflare Access, every request the edge forwards
carries a ``Cf-Access-Jwt-Assertion`` header: an RS256 JWT signed by the
team's keys, minted only after Access authenticated the requester (browser
SSO or a service token). Verifying that signature is strictly stronger auth
than a shared password — per-identity, revocable at the edge, and it removes
the browser's Basic-auth prompt entirely (the "second login" after SSO).

Configuration (config.py):
  CF_ACCESS_TEAM_DOMAIN  e.g. "cyberlion.cloudflareaccess.com"
  CF_ACCESS_AUD          comma-separated Access application AUD tags (one per
                         Access app that fronts this host, e.g. the UI app and
                         the /mcp app)

Verification checks signature (against the team's published JWKS), issuer,
audience, and expiry. The JWKS is cached and refreshed on unknown key ids,
so Cloudflare key rotation works without restarts.
"""

import json
import logging
import threading
import time
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 3600


class AccessJWTVerifier:
    def __init__(self, team_domain: str, audiences: list[str]):
        self.team_domain = team_domain.strip().rstrip("/")
        if self.team_domain.startswith("http"):
            self.team_domain = self.team_domain.split("://", 1)[1]
        self.issuer = f"https://{self.team_domain}"
        self.certs_url = f"{self.issuer}/cdn-cgi/access/certs"
        self.audiences = [a.strip() for a in audiences if a.strip()]
        self._keys: Dict[str, Any] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _refresh_keys(self, force: bool = False):
        with self._lock:
            if not force and self._keys and time.time() - self._fetched_at < _JWKS_TTL_SECONDS:
                return
            from jwt.algorithms import RSAAlgorithm

            with urllib.request.urlopen(self.certs_url, timeout=10) as resp:
                jwks = json.load(resp)
            keys = {}
            for key in jwks.get("keys", []):
                if key.get("kid"):
                    keys[key["kid"]] = RSAAlgorithm.from_jwk(json.dumps(key))
            if keys:
                self._keys = keys
                self._fetched_at = time.time()
                logger.info(f"Access JWKS refreshed: {len(keys)} keys from {self.certs_url}")

    def verify(self, token: str) -> Optional[Dict[str, Any]]:
        """Return the JWT claims if valid for this deployment, else None."""
        import jwt

        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError:
            return None
        if not kid:
            return None

        self._refresh_keys()
        if kid not in self._keys:
            # Unknown kid — likely key rotation; force one refresh.
            try:
                self._refresh_keys(force=True)
            except Exception as e:
                logger.warning(f"Access JWKS refresh failed: {e}")
                return None
        key = self._keys.get(kid)
        if key is None:
            return None

        for aud in self.audiences:
            try:
                return jwt.decode(
                    token,
                    key=key,
                    algorithms=["RS256"],
                    audience=aud,
                    issuer=self.issuer,
                )
            except jwt.InvalidAudienceError:
                continue
            except jwt.PyJWTError:
                return None
        return None

    @staticmethod
    def identity_from_claims(claims: Dict[str, Any]) -> str:
        """Human-readable identity: user email for SSO, token name for service auth."""
        return claims.get("email") or claims.get("common_name") or claims.get("sub") or "authenticated"


_verifier: Optional[AccessJWTVerifier] = None


def get_verifier() -> Optional[AccessJWTVerifier]:
    """Singleton built from settings; None when Access trust isn't configured."""
    global _verifier
    from config import settings

    if not settings.cf_access_team_domain or not settings.cf_access_aud:
        return None
    auds = settings.cf_access_aud.split(",")
    if (
        _verifier is None
        or _verifier.team_domain != settings.cf_access_team_domain.strip().rstrip("/").removeprefix("https://")
        or _verifier.audiences != [a.strip() for a in auds if a.strip()]
    ):
        _verifier = AccessJWTVerifier(settings.cf_access_team_domain, auds)
    return _verifier
