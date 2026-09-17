"""Verified request identity, from whichever source the site actually runs.

``PRIVATE_COLLECTIONS`` enforces per-person ownership, and it is only as
trustworthy as the identity it enforces against. Cloudflare Access was the
first source this app accepted (services/access_jwt.py) and it remains the
right answer for an internet-facing deployment. It is the wrong answer — and
frequently an impossible one — for an internal or air-gapped install, where
there is no Cloudflare in the path and the agency already runs its own
identity provider.

This module is the seam. One verifier per deployment, chosen by
``IDENTITY_PROVIDER``:

  cloudflare_access  Cf-Access-Jwt-Assertion, verified against the team's
                     JWKS. The historical default; auto-selected when
                     CF_ACCESS_* are set and IDENTITY_PROVIDER is empty.
  oidc               Authorization: Bearer <JWT> from the site's own IdP
                     (Keycloak, Entra ID, Okta, PingFederate). Signature,
                     issuer, audience and expiry are verified against the
                     issuer's JWKS; an optional claim requirement narrows
                     admission further (e.g. groups=asymptote-users).
  trusted_header     An authenticating reverse proxy (mTLS terminator, SSO
                     proxy, SPNEGO/Kerberos front end) has already
                     authenticated the caller and passes the result in a
                     header.

The trusted-header mode is the one that can be forged, so it refuses to
start without a guard that actually holds. Two do:

  * the app is bound to loopback, so nothing but a process on the host —
    i.e. the proxy — can open a connection to it at all; or
  * the proxy signs the identity with a shared secret (HMAC-SHA256), which
    holds no matter what the network path looks like.

``TRUSTED_HEADER_PROXIES`` is available on top of those, but never as the
only guard, and the distinction matters: uvicorn's proxy-header handling
rewrites the peer address from ``X-Forwarded-For`` for connections from
``FORWARDED_ALLOW_IPS`` (127.0.0.1 by default), so the address this code
sees is not always the real socket peer. A check that an attacker can
influence is defence in depth, not a boundary. This is the same reason the
rest of the app refuses half-boundaries rather than shipping them.

Every backend answers the same question: given this request's headers and
peer address, who is this? A string identity, or None. Callers never see
raw claims; ``middleware/user_context.py`` and the ownership choke points
consume the string.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import threading
import time
import urllib.request
from typing import Any, Dict, Mapping, Optional, Protocol

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 3600

# Providers that establish identity. "" means auto-detect (Cloudflare when
# configured, otherwise no verified identity at all).
IDENTITY_PROVIDERS = ("cloudflare_access", "oidc", "trusted_header")


class IdentityVerifier(Protocol):
    """Resolves a request to a verified identity string, or None."""

    via: str

    def has_candidate(self, headers: Mapping[str, str]) -> bool:
        """Cheap, I/O-free: could this request carry an identity at all?

        The auth middleware runs ``verify_request`` in a worker thread, since
        verifying can refresh a key set over the network. That threadpool is
        shared with chat and indexing, so a request carrying no credential
        must not spend a slot to learn it carries no credential.
        """
        ...

    def verify_request(
        self, headers: Mapping[str, str], client_ip: Optional[str]
    ) -> Optional[str]:
        ...


# ── Shared JWKS cache ───────────────────────────────────────────────────────
# Both JWT backends need the same thing: fetch a key set, cache it, and
# refresh once on an unknown key id so the IdP can rotate keys without a
# restart. Kept here rather than duplicated per backend.


class JWKSCache:
    def __init__(self, url: str):
        self.url = url
        self._keys: Dict[str, Any] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _load(self, force: bool = False) -> None:
        with self._lock:
            if not force and self._keys and time.time() - self._fetched_at < _JWKS_TTL_SECONDS:
                return
            from jwt.algorithms import RSAAlgorithm, ECAlgorithm

            with urllib.request.urlopen(self.url, timeout=10) as resp:
                jwks = json.load(resp)
            keys: Dict[str, Any] = {}
            for key in jwks.get("keys", []):
                kid = key.get("kid")
                if not kid:
                    continue
                kty = key.get("kty")
                try:
                    if kty == "RSA":
                        keys[kid] = RSAAlgorithm.from_jwk(json.dumps(key))
                    elif kty == "EC":
                        keys[kid] = ECAlgorithm.from_jwk(json.dumps(key))
                except Exception as e:
                    logger.warning(f"JWKS key {kid} could not be parsed: {e}")
            if keys:
                self._keys = keys
                self._fetched_at = time.time()
                logger.info(f"JWKS refreshed: {len(keys)} keys from {self.url}")

    def get(self, kid: str) -> Optional[Any]:
        """Return the signing key for ``kid``, refreshing once if unknown."""
        try:
            self._load()
        except Exception as e:
            logger.warning(f"JWKS fetch failed for {self.url}: {e}")
            if not self._keys:
                return None
        if kid not in self._keys:
            try:
                self._load(force=True)
            except Exception as e:
                logger.warning(f"JWKS refresh failed for {self.url}: {e}")
                return None
        return self._keys.get(kid)


# ── Cloudflare Access ───────────────────────────────────────────────────────


class CloudflareAccessIdentity:
    """Adapter over the existing Access verifier, unchanged in behaviour."""

    via = "cloudflare-access"

    def __init__(self, verifier):
        self._verifier = verifier

    def has_candidate(self, headers: Mapping[str, str]) -> bool:
        return bool(headers.get("cf-access-jwt-assertion"))

    def verify_request(
        self, headers: Mapping[str, str], client_ip: Optional[str]
    ) -> Optional[str]:
        assertion = headers.get("cf-access-jwt-assertion", "")
        if not assertion:
            return None
        claims = self._verifier.verify(assertion)
        if claims is None:
            return None
        return self._verifier.identity_from_claims(claims)


# ── Generic OIDC / OAuth 2 bearer ───────────────────────────────────────────


class OIDCIdentity:
    """Validate a bearer JWT minted by the site's own identity provider.

    The token arrives as ``Authorization: Bearer <jwt>`` — the same header
    the shared password and personal MCP tokens use, so this backend only
    considers values that look like a JWS (three dot-separated segments).
    A password or an ``asy_mcp_`` token is left for main.py's other
    branches, which is what lets all three coexist on one deployment.
    """

    via = "oidc"

    def __init__(
        self,
        issuer: str,
        audiences: list[str],
        jwks_url: str = "",
        identity_claim: str = "email",
        required_claims: str = "",
    ):
        self.issuer = issuer.strip().rstrip("/")
        self.audiences = [a.strip() for a in audiences if a.strip()]
        self.identity_claim = identity_claim.strip() or "email"
        self.required = _parse_required_claims(required_claims)
        self._explicit_jwks_url = jwks_url.strip()
        self._jwks: Optional[JWKSCache] = None
        self._discovery_lock = threading.Lock()

    def _jwks_cache(self) -> Optional[JWKSCache]:
        if self._jwks is not None:
            return self._jwks
        with self._discovery_lock:
            if self._jwks is not None:
                return self._jwks
            url = self._explicit_jwks_url
            if not url:
                # Standard OIDC discovery. An air-gapped install that cannot
                # reach the discovery document can set OIDC_JWKS_URL directly.
                discovery = f"{self.issuer}/.well-known/openid-configuration"
                try:
                    with urllib.request.urlopen(discovery, timeout=10) as resp:
                        url = json.load(resp).get("jwks_uri", "")
                except Exception as e:
                    logger.warning(f"OIDC discovery failed at {discovery}: {e}")
                    return None
            if not url:
                logger.warning(f"OIDC discovery at {self.issuer} advertised no jwks_uri")
                return None
            self._jwks = JWKSCache(url)
            return self._jwks

    @staticmethod
    def _bearer_jws(headers: Mapping[str, str]) -> str:
        """The bearer value, but only when it is shaped like a JWS.

        A password or an ``asy_mcp_`` token rides the same header and belongs
        to main.py's other branches; leaving those alone is what lets all
        three credential kinds coexist on one deployment.
        """
        scheme, _, value = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return ""
        token = value.strip()
        return token if token.count(".") == 2 else ""

    def has_candidate(self, headers: Mapping[str, str]) -> bool:
        return bool(self._bearer_jws(headers))

    def verify_request(
        self, headers: Mapping[str, str], client_ip: Optional[str]
    ) -> Optional[str]:
        token = self._bearer_jws(headers)
        return self._verify_token(token) if token else None

    def _verify_token(self, token: str) -> Optional[str]:
        import jwt

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        kid = header.get("kid")
        alg = header.get("alg", "")
        if not kid or not alg.startswith(("RS", "ES", "PS")):
            # Symmetric or unsigned tokens are never accepted: the key would
            # have to be shared with every client that mints one.
            return None

        cache = self._jwks_cache()
        if cache is None:
            return None
        key = cache.get(kid)
        if key is None:
            return None

        claims = None
        for aud in self.audiences:
            try:
                claims = jwt.decode(
                    token,
                    key=key,
                    algorithms=[alg],
                    audience=aud,
                    issuer=self.issuer,
                    # A token with no expiry would be valid forever; PyJWT
                    # only checks `exp` when it is present, so require it.
                    options={"require": ["exp"]},
                )
                break
            except jwt.InvalidAudienceError:
                continue
            except jwt.PyJWTError:
                return None
        if claims is None:
            return None
        if not _claims_satisfy(claims, self.required):
            return None
        return self.identity_from_claims(claims)

    def identity_from_claims(self, claims: Dict[str, Any]) -> Optional[str]:
        value = claims.get(self.identity_claim)
        if not value:
            # preferred_username and sub are the usual fallbacks when the
            # deployment's IdP does not release email to this client.
            value = claims.get("preferred_username") or claims.get("sub")
        return str(value) if value else None


def _parse_required_claims(spec: str) -> list[tuple[str, str]]:
    """Parse "groups=asymptote-users, dept=research" into pairs."""
    pairs: list[tuple[str, str]] = []
    for clause in (spec or "").split(","):
        clause = clause.strip()
        if not clause or "=" not in clause:
            continue
        name, _, wanted = clause.partition("=")
        name, wanted = name.strip(), wanted.strip()
        if name and wanted:
            pairs.append((name, wanted))
    return pairs


def _claims_satisfy(claims: Mapping[str, Any], required: list[tuple[str, str]]) -> bool:
    """Every required claim must be present, matching scalar or list member."""
    for name, wanted in required:
        actual = claims.get(name)
        if actual is None:
            return False
        if isinstance(actual, (list, tuple, set)):
            if wanted not in {str(v) for v in actual}:
                return False
        elif str(actual) != wanted:
            return False
    return True


# ── Trusted reverse-proxy header ────────────────────────────────────────────


class TrustedHeaderIdentity:
    """Accept an identity a front-end proxy already authenticated.

    This is the mode that covers mTLS (the terminator passes the certificate
    subject), Kerberos/SPNEGO, and any SSO proxy the site already operates.
    The header is trivially forgeable by anything that can reach the app
    directly, so a guard is mandatory — enforced at startup by
    ``validate_config``, not here, so a misconfiguration fails loudly rather
    than silently admitting everyone.

    The optional CIDR check is defence in depth only: see this module's
    docstring on why the peer address is not always the real socket peer.
    """

    via = "trusted-header"

    def __init__(
        self,
        header_name: str,
        trusted_proxies: str = "",
        shared_secret: str = "",
        signature_header: str = "",
    ):
        self.header_name = (header_name or "").strip().lower()
        self.networks = _parse_networks(trusted_proxies)
        self.shared_secret = (shared_secret or "").strip()
        self.signature_header = (signature_header or "").strip().lower()

    def has_candidate(self, headers: Mapping[str, str]) -> bool:
        return bool((headers.get(self.header_name) or "").strip())

    def verify_request(
        self, headers: Mapping[str, str], client_ip: Optional[str]
    ) -> Optional[str]:
        identity = (headers.get(self.header_name) or "").strip()
        if not identity:
            return None

        if self.networks and not _ip_in_networks(client_ip, self.networks):
            logger.warning(
                "Rejected %s header from %s: not a trusted proxy address.",
                self.header_name,
                client_ip or "unknown",
            )
            return None

        if self.shared_secret:
            presented = (headers.get(self.signature_header) or "").strip()
            expected = hmac.new(
                self.shared_secret.encode("utf-8"),
                identity.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if not presented or not hmac.compare_digest(presented.lower(), expected):
                logger.warning(
                    "Rejected %s header: signature missing or invalid.", self.header_name
                )
                return None

        return identity


def _parse_networks(spec: str) -> list:
    networks = []
    for raw in (spec or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            logger.warning(f"TRUSTED_HEADER_PROXIES entry {raw!r} is not a valid CIDR or address")
    return networks


def _ip_in_networks(client_ip: Optional[str], networks: list) -> bool:
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


# ── Selection ───────────────────────────────────────────────────────────────


def active_provider() -> str:
    """The provider this deployment uses: an explicit choice, or auto-detect.

    Auto-detect keeps every existing Cloudflare deployment working without a
    new setting: CF_ACCESS_* configured means cloudflare_access.
    """
    from config import settings

    explicit = (settings.identity_provider or "").strip().lower()
    if explicit:
        return explicit
    if settings.cf_access_team_domain and settings.cf_access_aud:
        return "cloudflare_access"
    return ""


def validate_config() -> None:
    """Raise RuntimeError on an identity configuration that cannot be trusted.

    Called from main.py's startup posture check, next to the other
    deployment-shape refusals, so a broken identity source is a boot failure
    rather than a silent downgrade to "everyone is anonymous".
    """
    from config import settings

    provider = active_provider()
    if not provider:
        return
    if provider not in IDENTITY_PROVIDERS:
        raise RuntimeError(
            f"IDENTITY_PROVIDER={provider!r} is not a provider this app knows. "
            f"Use one of: {', '.join(IDENTITY_PROVIDERS)}."
        )

    if provider == "cloudflare_access":
        if not (settings.cf_access_team_domain and settings.cf_access_aud):
            raise RuntimeError(
                "IDENTITY_PROVIDER=cloudflare_access needs CF_ACCESS_TEAM_DOMAIN "
                "and CF_ACCESS_AUD. See docs/DEPLOYMENT.md."
            )

    elif provider == "oidc":
        if not settings.oidc_issuer:
            raise RuntimeError(
                "IDENTITY_PROVIDER=oidc needs OIDC_ISSUER - the issuer URL of "
                "your identity provider, e.g. "
                "https://sso.agency.gov/realms/main. See docs/IDENTITY.md."
            )
        if not settings.oidc_audience:
            raise RuntimeError(
                "IDENTITY_PROVIDER=oidc needs OIDC_AUDIENCE - the client id or "
                "API audience tokens for this app carry. Without it any token "
                "your IdP ever issued, for any application, would be accepted "
                "here. See docs/IDENTITY.md."
            )
        if not settings.oidc_issuer.lower().startswith("https://") and not _is_local_issuer(
            settings.oidc_issuer
        ):
            raise RuntimeError(
                "OIDC_ISSUER must be https:// - token signatures are fetched "
                "from it, so a plaintext issuer lets anyone on the path mint "
                "identities. Use https, or a loopback issuer for local testing."
            )

    elif provider == "trusted_header":
        if not settings.trusted_header_name:
            raise RuntimeError(
                "IDENTITY_PROVIDER=trusted_header needs TRUSTED_HEADER_NAME - "
                "the header your proxy sets, e.g. X-Forwarded-User. See "
                "docs/IDENTITY.md."
            )
        # A guard that actually holds: loopback binding (only a process on
        # this host can connect) or an HMAC the proxy computes. A CIDR
        # allowlist is NOT sufficient on its own - see the module docstring.
        bound_loopback = _is_loopback_host(settings.host)
        if not bound_loopback and not settings.trusted_header_secret:
            raise RuntimeError(
                "IDENTITY_PROVIDER=trusted_header needs a guard that holds. "
                f"This deployment binds {settings.host!r}, so anything that can "
                "reach the port can set "
                f"{settings.trusted_header_name} and become anyone. Either set "
                "HOST=127.0.0.1 so your proxy is the only route in, or set "
                "TRUSTED_HEADER_SECRET so the proxy signs the identity. "
                "TRUSTED_HEADER_PROXIES alone does not count: uvicorn rewrites "
                "the peer address from X-Forwarded-For, so the value checked "
                "is not always the real socket peer. See docs/IDENTITY.md."
            )
        if settings.trusted_header_secret and not settings.trusted_header_signature_name:
            raise RuntimeError(
                "TRUSTED_HEADER_SECRET is set but TRUSTED_HEADER_SIGNATURE_NAME "
                "is empty - the signature would never be read. Name the header "
                "your proxy puts the HMAC in."
            )
        if bound_loopback and not settings.trusted_header_secret:
            logger.info(
                "Identity: trusted header %s, guarded by the loopback bind. "
                "Anything able to run on this host can impersonate any user; "
                "set TRUSTED_HEADER_SECRET to close that too.",
                settings.trusted_header_name,
            )


def _is_loopback_host(host: str) -> bool:
    host = (host or "").strip().strip("[]").lower()
    if host in {"localhost", ""}:
        return host == "localhost"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local_issuer(issuer: str) -> bool:
    lowered = issuer.lower()
    return lowered.startswith("http://localhost") or lowered.startswith("http://127.0.0.1")


# Only the OIDC backend is cached, and only because it owns a JWKS cache
# worth keeping warm across requests. The other two are cheap value objects
# built per request on purpose: Cloudflare's delegates to access_jwt, which
# does its own caching and can be swapped underneath us (config reload, and
# every test that substitutes a verifier), so holding a wrapper around a
# stale one would silently reject valid identities.
_oidc_verifier: Optional[OIDCIdentity] = None
_oidc_signature: Optional[tuple] = None


def get_identity_verifier() -> Optional[IdentityVerifier]:
    """The deployment's verifier, or None when no identity source is configured."""
    global _oidc_verifier, _oidc_signature
    from config import settings

    provider = active_provider()
    if not provider:
        return None

    if provider == "cloudflare_access":
        from services.access_jwt import get_verifier as get_access_verifier

        access = get_access_verifier()
        return CloudflareAccessIdentity(access) if access is not None else None

    if provider == "oidc":
        signature = (
            settings.oidc_issuer,
            settings.oidc_audience,
            settings.oidc_jwks_url,
            settings.oidc_identity_claim,
            settings.oidc_required_claims,
        )
        if _oidc_verifier is None or _oidc_signature != signature:
            _oidc_verifier = OIDCIdentity(
                issuer=settings.oidc_issuer,
                audiences=settings.oidc_audience.split(","),
                jwks_url=settings.oidc_jwks_url,
                identity_claim=settings.oidc_identity_claim,
                required_claims=settings.oidc_required_claims,
            )
            _oidc_signature = signature
        return _oidc_verifier

    if provider == "trusted_header":
        return TrustedHeaderIdentity(
            header_name=settings.trusted_header_name,
            trusted_proxies=settings.trusted_header_proxies,
            shared_secret=settings.trusted_header_secret,
            signature_header=settings.trusted_header_signature_name,
        )

    return None
