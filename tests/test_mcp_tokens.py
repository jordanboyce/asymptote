"""Personal MCP access tokens: the DB layer, the service, and the auth
middleware branch that accepts one only on /mcp paths (never elsewhere).
"""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from services.app_database import SQLiteBackend
from services import mcp_tokens


def _fake_request(path: str, token: str) -> Request:
    """A minimal Request for exercising main.require_auth directly.

    Avoids needing the real app's lifespan (which starts the mounted MCP
    session manager's task group) just to prove the auth middleware itself
    let a request through — the mounted app's own behavior isn't this
    module's concern.
    """
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "query_string": b"",
    }
    return Request(scope)


async def _call_next_marker(request):
    return "downstream-called"


# ── DB layer ─────────────────────────────────────────────────────────────

def test_create_list_get_touch_revoke_roundtrip(tmp_path: Path):
    db = SQLiteBackend(tmp_path / "app.db")

    created = db.create_mcp_token("alice", "MacBook", "hash-1", "asy_mcp_ab12cd")
    assert created["user_id"] == "alice"
    assert created["name"] == "MacBook"
    assert created["last_used_at"] is None
    assert created["revoked_at"] is None
    assert "token_hash" not in created  # never echoed back

    fetched = db.get_mcp_token_by_hash("hash-1")
    assert fetched["id"] == created["id"]
    assert fetched["token_hash"] == "hash-1"

    assert db.get_mcp_token_by_hash("no-such-hash") is None

    mine = db.list_mcp_tokens("alice")
    assert len(mine) == 1
    assert mine[0]["id"] == created["id"]
    assert db.list_mcp_tokens("bob") == []

    db.touch_mcp_token(created["id"])
    assert db.get_mcp_token_by_hash("hash-1")["last_used_at"] is not None

    assert db.revoke_mcp_token(created["id"], "alice") is True
    assert db.get_mcp_token_by_hash("hash-1")["revoked_at"] is not None
    # Already revoked: revoking again reports nothing to do.
    assert db.revoke_mcp_token(created["id"], "alice") is False


def test_revoke_requires_matching_owner(tmp_path: Path):
    db = SQLiteBackend(tmp_path / "app.db")
    created = db.create_mcp_token("alice", "Phone", "hash-2", "asy_mcp_zz99")
    assert db.revoke_mcp_token(created["id"], "bob") is False
    assert db.get_mcp_token_by_hash("hash-2")["revoked_at"] is None


def test_anonymous_tokens_use_null_user_id(tmp_path: Path):
    """Shared-appliance / password-only mode: user_id is None throughout."""
    db = SQLiteBackend(tmp_path / "app.db")
    created = db.create_mcp_token(None, "Shared laptop", "hash-3", "asy_mcp_qq")
    assert created["user_id"] is None

    mine = db.list_mcp_tokens(None)
    assert [t["id"] for t in mine] == [created["id"]]

    # Someone else's tokens (a real user_id) must not show up here.
    db.create_mcp_token("alice", "Alice's laptop", "hash-4", "asy_mcp_rr")
    assert db.list_mcp_tokens(None) == mine

    assert db.revoke_mcp_token(created["id"], None) is True


# ── Service layer ────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_app_db(tmp_path, monkeypatch):
    """Point the app_db singleton at a throwaway SQLite file for this test."""
    from services import app_database

    monkeypatch.setattr(app_database.app_db, "db_path", tmp_path / "app.db")
    app_database.app_db._init_db()
    return app_database.app_db


def test_generate_token_shape_and_secrecy(fresh_app_db):
    record = mcp_tokens.generate_token("alice", "  My Laptop  ")
    assert record["name"] == "My Laptop"
    assert record["token"].startswith(mcp_tokens.TOKEN_PREFIX)
    assert record["token_prefix"] == record["token"][: len(record["token_prefix"])]
    # The persisted row never carries the plaintext or exposes only the hash.
    stored = fresh_app_db.get_mcp_token_by_hash(mcp_tokens._hash(record["token"]))
    assert stored is not None
    assert stored["token_hash"] != record["token"]


def test_generate_token_default_name(fresh_app_db):
    record = mcp_tokens.generate_token("alice", "   ")
    assert record["name"] == "Unnamed device"


def test_verify_token_success_and_last_used(fresh_app_db):
    record = mcp_tokens.generate_token("alice", "Laptop")
    verified = mcp_tokens.verify_token(record["token"])
    assert verified is not None
    assert verified["user_id"] == "alice"

    refreshed = fresh_app_db.get_mcp_token_by_hash(mcp_tokens._hash(record["token"]))
    assert refreshed["last_used_at"] is not None


@pytest.mark.parametrize("bad", ["", "garbage", "Bearer something", "asy_mcp_" ])
def test_verify_token_rejects_junk(fresh_app_db, bad):
    assert mcp_tokens.verify_token(bad) is None


def test_verify_token_rejects_revoked(fresh_app_db):
    record = mcp_tokens.generate_token("alice", "Laptop")
    assert mcp_tokens.revoke_token(record["id"], "alice") is True
    assert mcp_tokens.verify_token(record["token"]) is None


def test_verify_token_rejects_password_lookalike(fresh_app_db):
    """A caller trying the AUTH_PASSWORD (or any non-prefixed secret) as an
    MCP token must fail the cheap prefix check, never reach the database."""
    assert mcp_tokens.verify_token("some-shared-password") is None


# ── Auth middleware: scoped to /mcp, never anywhere else ────────────────

SECRET = "test-deploy-secret"


@pytest.fixture()
def token_client(tmp_path, monkeypatch):
    """A TestClient with AUTH_PASSWORD set and a fresh app_db, so a minted
    personal MCP token and the shared password can be told apart.
    """
    import config
    import main
    from services import app_database

    monkeypatch.setenv("AUTH_PASSWORD", SECRET)
    importlib.reload(config)
    main = importlib.reload(main)

    monkeypatch.setattr(app_database.app_db, "db_path", tmp_path / "app.db")
    app_database.app_db._init_db()

    record = mcp_tokens.generate_token("alice", "Test Laptop")

    yield TestClient(main.app), record["token"], main

    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    importlib.reload(config)
    importlib.reload(main)


def test_mcp_token_accepted_on_mcp_path(token_client):
    _, token, main = token_client
    import asyncio

    request = _fake_request("/mcp/", token)
    result = asyncio.run(main.require_auth(request, _call_next_marker))
    # The mounted MCP app's own protocol handling isn't this module's
    # concern — only that the auth middleware let the request through to it.
    assert result == "downstream-called"
    assert request.state.auth_via == "mcp_token"
    assert request.state.auth_identity == "alice"


def test_mcp_token_rejected_on_non_mcp_path(token_client):
    client, token, _ = token_client
    response = client.get("/api/mcp/config", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_revoked_mcp_token_rejected(token_client):
    client, token, _ = token_client
    from services.app_database import app_db

    stored = app_db.get_mcp_token_by_hash(mcp_tokens._hash(token))
    app_db.revoke_mcp_token(stored["id"], "alice")

    response = client.post("/mcp/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_password_still_works_on_mcp_path(token_client):
    _, _, main = token_client
    import asyncio

    request = _fake_request("/mcp/", SECRET)
    result = asyncio.run(main.require_auth(request, _call_next_marker))
    assert result == "downstream-called"
    assert request.state.auth_via == "password"
