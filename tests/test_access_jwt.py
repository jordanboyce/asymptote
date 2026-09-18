"""Cloudflare Access JWT trust: signature, issuer, audience, expiry, fallback."""

import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services.access_jwt import AccessJWTVerifier

TEAM = "testteam.cloudflareaccess.com"
AUD_UI = "aud-ui-tag"
AUD_MCP = "aud-mcp-tag"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, other


def _jwk(private_key, kid):
    pub = private_key.public_key()
    numbers = pub.public_numbers()

    def b64(n, length):
        import base64
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    return {
        "kid": kid,
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "n": b64(numbers.n, 256),
        "e": b64(numbers.e, 3),
    }


@pytest.fixture()
def verifier(keypair, monkeypatch):
    key, _ = keypair
    v = AccessJWTVerifier(TEAM, [AUD_UI, AUD_MCP])
    jwks = {"keys": [_jwk(key, "kid-1")]}
    monkeypatch.setattr(
        "services.access_jwt.urllib.request.urlopen",
        lambda url, timeout=10: _FakeResp(jwks),
    )
    return v


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mint(private_key, kid="kid-1", **overrides):
    claims = {
        "aud": [AUD_UI],
        "iss": f"https://{TEAM}",
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        "email": "jordan@example.com",
    }
    claims.update(overrides)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pyjwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


def test_valid_token_returns_claims(verifier, keypair):
    key, _ = keypair
    claims = verifier.verify(_mint(key))
    assert claims is not None
    assert claims["email"] == "jordan@example.com"


def test_second_audience_accepted(verifier, keypair):
    key, _ = keypair
    assert verifier.verify(_mint(key, aud=[AUD_MCP])) is not None


def test_wrong_audience_rejected(verifier, keypair):
    key, _ = keypair
    assert verifier.verify(_mint(key, aud=["someone-elses-app"])) is None


def test_wrong_issuer_rejected(verifier, keypair):
    key, _ = keypair
    assert verifier.verify(_mint(key, iss="https://evil.example.com")) is None


def test_expired_rejected(verifier, keypair):
    key, _ = keypair
    assert verifier.verify(_mint(key, exp=int(time.time()) - 10)) is None


def test_wrong_signing_key_rejected(verifier, keypair):
    _, other = keypair
    assert verifier.verify(_mint(other, kid="kid-1")) is None


def test_garbage_token_rejected(verifier):
    assert verifier.verify("not.a.jwt") is None
    assert verifier.verify("") is None


def test_service_token_identity(verifier, keypair):
    key, _ = keypair
    claims = verifier.verify(_mint(key, email=None, common_name="clio-mcp"))
    assert AccessJWTVerifier.identity_from_claims(claims) == "clio-mcp"
