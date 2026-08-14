"""Identity resolution: single-user, trusted-proxy, and Cloudflare Access.

The property under test is mostly a negative one — that an unauthenticated
request in multi-user mode is *refused* rather than quietly resolving to the
default user. That fallback used to exist, and on a machine upgraded from
single-user mode the default user owns everything, so the bypass and the
jackpot were the same account.

RS256 signing here is real: a keypair is generated per test session, the
verifier's JWKS lookup is pointed at it, and tokens are signed and verified for
actual cryptographic correctness rather than asserted against a mock.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from middleware.user_context import get_current_user_id
from services import access_auth
from services.access_auth import AccessAuthError, AccessVerifier, _normalize_team_domain

TEAM = "acme.cloudflareaccess.com"
ISSUER = f"https://{TEAM}"
AUD = "a" * 64
KID = "test-key-1"


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return key, private_pem


@pytest.fixture
def verifier(keypair, monkeypatch):
    """An AccessVerifier whose JWKS lookup returns our local public key."""
    key, _ = keypair
    public_key = key.public_key()

    v = AccessVerifier(team_domain=TEAM, audience=AUD)

    class _FakeJWKClient:
        def __init__(self, *a, **kw):
            pass

        def get_signing_key_from_jwt(self, token):
            class _K:
                pass

            k = _K()
            k.key = public_key
            return k

    monkeypatch.setattr("jwt.PyJWKClient", _FakeJWKClient)
    return v


def make_token(keypair, **overrides):
    _, private_pem = keypair
    now = int(time.time())
    claims = {
        "email": "Tester@Example.com",
        "sub": "user-123",
        "aud": AUD,
        "iss": ISSUER,
        "iat": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    for k in [k for k, v in claims.items() if v is None]:
        del claims[k]
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": KID})


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _clean_verifier_cache():
    access_auth.reset_verifier_cache()
    yield
    access_auth.reset_verifier_cache()


# ── team-domain normalization ─────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("acme", "acme.cloudflareaccess.com"),
    ("acme.cloudflareaccess.com", "acme.cloudflareaccess.com"),
    ("https://acme.cloudflareaccess.com", "acme.cloudflareaccess.com"),
    ("https://acme.cloudflareaccess.com/", "acme.cloudflareaccess.com"),
    ("  ACME  ", "acme.cloudflareaccess.com"),
    ("", ""),
])
def test_team_domain_normalization(raw, expected):
    # Operators paste this out of the dashboard in whatever shape it appears;
    # a stray slash otherwise breaks issuer comparison with no useful error.
    assert _normalize_team_domain(raw) == expected


# ── token verification ────────────────────────────────────────────────────

def test_valid_token_yields_lowercased_email(verifier, keypair):
    user_id, display = verifier.identity_from(make_token(keypair))
    assert user_id == "tester@example.com"
    assert display == "tester@example.com"


def test_wrong_audience_is_rejected(verifier, keypair):
    # The AUD tag is what binds a token to *this* application. Without the
    # check, a token minted for any other app in the same Zero Trust account
    # would be accepted here.
    token = make_token(keypair, aud="b" * 64)
    with pytest.raises(AccessAuthError):
        verifier.identity_from(token)


def test_wrong_issuer_is_rejected(verifier, keypair):
    token = make_token(keypair, iss="https://attacker.cloudflareaccess.com")
    with pytest.raises(AccessAuthError):
        verifier.identity_from(token)


def test_expired_token_is_rejected(verifier, keypair):
    now = int(time.time())
    token = make_token(keypair, iat=now - 7200, exp=now - 3600)
    with pytest.raises(AccessAuthError):
        verifier.identity_from(token)


def test_unsigned_token_is_rejected(verifier):
    # The "alg: none" classic. PyJWT is asked for RS256 explicitly, so this
    # should never get past the algorithm check.
    now = int(time.time())
    token = jwt.encode(
        {"email": "x@y.z", "aud": AUD, "iss": ISSUER, "iat": now, "exp": now + 600},
        key="",
        algorithm="none",
    )
    with pytest.raises(AccessAuthError):
        verifier.identity_from(token)


def test_token_signed_by_a_different_key_is_rejected(verifier):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = int(time.time())
    token = jwt.encode(
        {"email": "x@y.z", "aud": AUD, "iss": ISSUER, "iat": now, "exp": now + 600},
        pem,
        algorithm="RS256",
        headers={"kid": KID},
    )
    with pytest.raises(AccessAuthError):
        verifier.identity_from(token)


def test_service_token_without_email_is_rejected(verifier, keypair):
    # Service tokens authenticate a machine, not a person. Mapping one onto a
    # user id would give automation somebody's collections.
    token = make_token(keypair, email=None, common_name="ci-runner.example")
    with pytest.raises(AccessAuthError):
        verifier.identity_from(token)


def test_empty_token_is_rejected(verifier):
    with pytest.raises(AccessAuthError):
        verifier.identity_from("")


# ── middleware wiring ─────────────────────────────────────────────────────

def test_single_user_mode_needs_no_headers(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "enable_multi_user", False)
    assert get_current_user_id(FakeRequest()) == settings.default_user_id


def test_multi_user_with_no_identity_source_refuses(monkeypatch):
    """The regression that matters most.

    Multi-user on, nothing configured to establish identity: the old code
    returned ``default_user_id`` — full access to every collection on a
    machine upgraded from single-user mode.
    """
    from config import settings
    monkeypatch.setattr(settings, "enable_multi_user", True)
    monkeypatch.setattr(settings, "access_team_domain", "")
    monkeypatch.setattr(settings, "access_aud", "")
    monkeypatch.setattr(settings, "trust_proxy_user_header", False)

    with pytest.raises(HTTPException) as exc:
        get_current_user_id(FakeRequest())
    assert exc.value.status_code == 401


def test_multi_user_ignores_plain_email_header_when_access_configured(monkeypatch, verifier, keypair):
    """A forged plaintext header must not beat the signature requirement."""
    from config import settings
    monkeypatch.setattr(settings, "enable_multi_user", True)
    monkeypatch.setattr(settings, "access_team_domain", TEAM)
    monkeypatch.setattr(settings, "access_aud", AUD)
    monkeypatch.setattr(access_auth, "get_verifier", lambda: verifier)

    req = FakeRequest({"Cf-Access-Authenticated-User-Email": "attacker@evil.example"})
    with pytest.raises(HTTPException) as exc:
        get_current_user_id(req)
    assert exc.value.status_code == 401


def test_multi_user_accepts_verified_jwt(monkeypatch, verifier, keypair):
    from config import settings
    import middleware.user_context as uc

    monkeypatch.setattr(settings, "enable_multi_user", True)
    monkeypatch.setattr(access_auth, "get_verifier", lambda: verifier)
    monkeypatch.setattr(uc, "_provision", lambda *a, **kw: None)

    req = FakeRequest({"Cf-Access-Jwt-Assertion": make_token(keypair)})
    assert get_current_user_id(req) == "tester@example.com"


def test_trusted_proxy_header_mode_is_opt_in(monkeypatch):
    from config import settings
    import middleware.user_context as uc

    monkeypatch.setattr(settings, "enable_multi_user", True)
    monkeypatch.setattr(settings, "access_team_domain", "")
    monkeypatch.setattr(settings, "access_aud", "")
    monkeypatch.setattr(settings, "trust_proxy_user_header", True)
    monkeypatch.setattr(uc, "_provision", lambda *a, **kw: None)

    assert get_current_user_id(FakeRequest({"X-User-ID": "Alice@Example.com"})) == "alice@example.com"

    # Still refuses when the proxy sent nothing.
    with pytest.raises(HTTPException):
        get_current_user_id(FakeRequest())


def test_provisioning_failure_does_not_deny_an_authenticated_user(monkeypatch, verifier, keypair):
    from config import settings
    import middleware.user_context as uc

    monkeypatch.setattr(settings, "enable_multi_user", True)
    monkeypatch.setattr(access_auth, "get_verifier", lambda: verifier)

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(uc.app_db, "upsert_user", _boom)

    req = FakeRequest({"Cf-Access-Jwt-Assertion": make_token(keypair)})
    assert get_current_user_id(req) == "tester@example.com"
