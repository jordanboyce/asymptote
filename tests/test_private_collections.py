"""Private-collections mode: ownership, sharing, and enforcement.

The mode is opt-in (PRIVATE_COLLECTIONS=true) and requires Cloudflare Access
so every request carries a verified identity. These tests cover the three
layers separately:

- service rules: check_collection_access / get_all_collections visibility
- startup posture: the flag refuses to start without an identity source
- HTTP enforcement: two identities exercising the real routers end-to-end
  (the Access verifier is faked; the JWT crypto itself is covered by
  test_access_jwt.py)
"""

import importlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from config import settings as _import_time_settings
from services.app_database import SQLiteBackend, app_db
from services.collection_service import collection_service
from services.sharing_service import sharing_service
from middleware.user_context import set_request_user, reset_request_user

ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture()
def fresh_db(tmp_path):
    """Point the app_db singleton (shared by every service) at a temp SQLite
    file, seeded with the default team collection, and restore afterwards."""
    old_db_path = app_db.db_path
    old_base_dir = collection_service.base_dir
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = tmp_path / "collections"
    yield app_db
    app_db.db_path = old_db_path
    collection_service.base_dir = old_base_dir


def _settings_objects():
    """Every Settings object a module in this process might hold.

    Modules bind `settings` at their import time; test fixtures that reload
    config (here and in test_auth.py) replace config.settings with a fresh
    object, so the import-time object and the current one can differ. Patching
    both keeps the flip visible to every module regardless of test order.
    """
    objs = {id(_import_time_settings): _import_time_settings}
    objs.setdefault(id(config.settings), config.settings)
    return list(objs.values())


def _flip_private_mode():
    saved = []
    for s in _settings_objects():
        saved.append((s, s.private_collections, s.cf_access_team_domain, s.cf_access_aud))
        s.private_collections = True
        s.cf_access_team_domain = "testteam.cloudflareaccess.com"
        s.cf_access_aud = "aud-test"
    return saved


def _restore(saved):
    for s, private, team, aud in saved:
        s.private_collections = private
        s.cf_access_team_domain = team
        s.cf_access_aud = aud


@pytest.fixture()
def private_mode(fresh_db):
    """Flip the shared settings object(s) into private-collections mode."""
    saved = _flip_private_mode()
    yield config.settings
    _restore(saved)


def _make_private(owner):
    return app_db.create_collection(name=f"{owner}'s notes", owner_id=owner)


# ── Service rules ───────────────────────────────────────────────────────────


def test_mode_off_everyone_is_owner(fresh_db):
    assert sharing_service.check_collection_access("anything", ALICE) == "owner"


def test_team_collection_open_to_all(private_mode):
    """The seeded 'default' collection is owned by 'default' → team tier."""
    assert sharing_service.check_collection_access("default", ALICE) == "owner"
    assert sharing_service.check_collection_access("default", BOB) == "owner"
    # Anonymous password callers reach team collections too
    assert sharing_service.check_collection_access("default", None) == "owner"


def test_private_collection_hidden_from_others(private_mode):
    cid = _make_private(ALICE)
    assert sharing_service.check_collection_access(cid, ALICE) == "owner"
    assert sharing_service.check_collection_access(cid, BOB) is None
    assert sharing_service.check_collection_access(cid, None) is None


def test_unknown_collection_is_none(private_mode):
    assert sharing_service.check_collection_access("nope1234", ALICE) is None


def test_share_grants_declared_permission(private_mode):
    cid = _make_private(ALICE)
    share = sharing_service.create_share(cid, ALICE, permission="read")
    sharing_service.accept_share(share["share_id"], BOB)
    assert sharing_service.check_collection_access(cid, BOB) == "read"

    cid2 = _make_private(ALICE)
    share2 = sharing_service.create_share(cid2, ALICE, permission="readwrite")
    sharing_service.accept_share(share2["share_id"], BOB)
    assert sharing_service.check_collection_access(cid2, BOB) == "readwrite"


def test_only_owner_can_share(private_mode):
    cid = _make_private(ALICE)
    with pytest.raises(PermissionError):
        sharing_service.create_share(cid, BOB)
    # Team collections have no person as owner — nobody can share them
    with pytest.raises(PermissionError):
        sharing_service.create_share("default", ALICE)


def test_anonymous_cannot_accept_shares(private_mode):
    cid = _make_private(ALICE)
    share = sharing_service.create_share(cid, ALICE)
    with pytest.raises(ValueError, match="identity"):
        sharing_service.accept_share(share["share_id"], None)


def test_get_all_collections_scoping(private_mode):
    mine = _make_private(ALICE)
    theirs = _make_private(BOB)

    alice_ids = {c["id"] for c in collection_service.get_all_collections(user_id=ALICE)}
    assert mine in alice_ids
    assert theirs not in alice_ids
    assert "default" in alice_ids  # team tier stays visible

    anon_ids = {c["id"] for c in collection_service.get_all_collections(user_id=None)}
    assert anon_ids == {"default"}

    share = sharing_service.create_share(theirs, BOB, permission="read")
    sharing_service.accept_share(share["share_id"], ALICE)
    alice_cols = collection_service.get_all_collections(user_id=ALICE)
    shared = next(c for c in alice_cols if c["id"] == theirs)
    assert shared["permission"] == "read"


def test_require_collection_access_status_codes(private_mode):
    from api.deps import require_collection_access

    cid = _make_private(ALICE)
    # Invisible == missing: 404, never 403, so ids can't be probed
    with pytest.raises(HTTPException) as exc:
        require_collection_access(cid, BOB)
    assert exc.value.status_code == 404

    share = sharing_service.create_share(cid, ALICE, permission="read")
    sharing_service.accept_share(share["share_id"], BOB)
    assert require_collection_access(cid, BOB) == "read"
    with pytest.raises(HTTPException) as exc:
        require_collection_access(cid, BOB, write=True)
    assert exc.value.status_code == 403


def test_mcp_resolver_masks_invisible_collections(private_mode):
    from services.mcp_server import _resolve_collection_id

    cid = _make_private(ALICE)
    token = set_request_user(BOB)
    try:
        with pytest.raises(ValueError, match="not found"):
            _resolve_collection_id(cid)
    finally:
        reset_request_user(token)

    token = set_request_user(ALICE)
    try:
        assert _resolve_collection_id(cid) == cid
    finally:
        reset_request_user(token)


# ── Startup posture ─────────────────────────────────────────────────────────


@pytest.fixture()
def reloaded_main(monkeypatch):
    """Reload config + main under caller-supplied env, then restore both."""
    import main

    touched = []

    def _reload(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
            touched.append(key)
        importlib.reload(config)
        return importlib.reload(main)

    yield _reload

    for key in touched:
        monkeypatch.delenv(key, raising=False)
    importlib.reload(config)
    # Restore object identity: modules imported before the reload hold the
    # original Settings instance, so leaving a fresh one in config.settings
    # would split the app between two configuration objects.
    config.settings = _import_time_settings
    importlib.reload(main)


def test_private_collections_refuse_without_identity_source(reloaded_main):
    """Ownership enforced against a forgeable identity is the half-boundary
    this app refuses to ship — no Access config, no private mode."""
    with pytest.raises(RuntimeError, match="identity"):
        # CF_ACCESS_* blanked explicitly: a developer .env may configure them
        reloaded_main(
            PRIVATE_COLLECTIONS="true",
            CF_ACCESS_TEAM_DOMAIN="",
            CF_ACCESS_AUD="",
        )


def test_private_collections_start_with_access_configured(reloaded_main):
    main = reloaded_main(
        PRIVATE_COLLECTIONS="true",
        CF_ACCESS_TEAM_DOMAIN="testteam.cloudflareaccess.com",
        CF_ACCESS_AUD="aud-test",
    )
    assert main.app is not None


# ── HTTP enforcement with two identities ────────────────────────────────────


class _FakeVerifier:
    """Stands in for AccessJWTVerifier: the 'assertion' is just the email.

    JWT crypto (signature/issuer/audience/expiry) is covered by
    test_access_jwt.py; here we only need the middleware to map an assertion
    to an identity.
    """

    def verify(self, token):
        return {"email": token} if token and "@" in token else None

    @staticmethod
    def identity_from_claims(claims):
        return claims.get("email")


@pytest.fixture()
def private_client(fresh_db, monkeypatch):
    """A TestClient for the app reloaded in private-collections mode.

    Settings are flipped on the shared singleton (visible to every module),
    the Access verifier is faked, and main is reloaded so the module-level
    auth-middleware registration runs for this mode. Teardown restores the
    settings first and only then reloads main, so the restored app matches
    the restored configuration.
    """
    import services.access_jwt as access_jwt
    import main

    saved = _flip_private_mode()
    monkeypatch.setattr(access_jwt, "get_verifier", lambda: _FakeVerifier())

    reloaded = importlib.reload(main)
    # Bare client (no context manager): lifespan skipped, middleware still runs.
    yield TestClient(reloaded.app)

    _restore(saved)
    importlib.reload(main)


def _as(email):
    return {"cf-access-jwt-assertion": email}


def test_requests_without_identity_are_rejected(private_client):
    assert private_client.get("/api/collections").status_code == 401
    assert private_client.get("/health").status_code == 200  # probes stay open


def test_identity_reported(private_client):
    r = private_client.get("/api/user/me", headers=_as(ALICE))
    assert r.status_code == 200
    assert r.json()["user_id"] == ALICE
    assert r.json()["private_collections"] is True


def test_create_with_team_visibility(private_client):
    """The creator can opt a new collection into the team tier."""
    r = private_client.post(
        "/api/collections",
        json={"name": "Shared docs", "visibility": "team"},
        headers=_as(ALICE),
    )
    assert r.status_code == 201
    assert r.json()["owner_id"] == "default"
    cid = r.json()["id"]
    # Team tier: visible to everyone, including a user who owns nothing
    bob_ids = {
        c["id"]
        for c in private_client.get("/api/collections", headers=_as(BOB)).json()["collections"]
    }
    assert cid in bob_ids


def test_collections_are_isolated_between_users(private_client):
    r = private_client.post(
        "/api/collections", json={"name": "Alice research"}, headers=_as(ALICE)
    )
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["owner_id"] == ALICE

    bob_ids = {
        c["id"]
        for c in private_client.get("/api/collections", headers=_as(BOB)).json()["collections"]
    }
    assert cid not in bob_ids
    assert "default" in bob_ids  # team collection stays shared

    # Direct id access answers like a missing collection
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 404
    assert private_client.get(f"/api/collections/{cid}", headers=_as(ALICE)).status_code == 200


def test_share_flow_end_to_end(private_client):
    cid = private_client.post(
        "/api/collections", json={"name": "Shared notes"}, headers=_as(ALICE)
    ).json()["id"]

    share = private_client.post(
        f"/api/collections/{cid}/share", json={"permission": "read"}, headers=_as(ALICE)
    )
    assert share.status_code == 201
    token = share.json()["share_id"]

    # Bob can't see it until he accepts the share
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 404
    accepted = private_client.post(f"/api/shares/{token}/accept", headers=_as(BOB))
    assert accepted.status_code == 200

    got = private_client.get(f"/api/collections/{cid}", headers=_as(BOB))
    assert got.status_code == 200
    assert got.json()["permission"] == "read"

    # read-only share: writes are rejected
    upd = private_client.put(
        f"/api/collections/{cid}", json={"name": "hijacked"}, headers=_as(BOB)
    )
    assert upd.status_code == 403

    # deletion stays owner-only even for share holders
    assert private_client.delete(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 403


def test_contextvar_identity_reaches_nested_enforcement(private_client):
    """Endpoints with no user dependency read the contextvar the middleware
    binds (the same path documents and /mcp enforcement uses)."""
    cid = private_client.post(
        "/api/collections", json={"name": "ctx check"}, headers=_as(ALICE)
    ).json()["id"]
    r = private_client.get(f"/api/collections/{cid}/expertise", headers=_as(BOB))
    assert r.status_code == 404
    r = private_client.get(f"/api/collections/{cid}/expertise", headers=_as(ALICE))
    assert r.status_code == 200


def test_readwrite_share_can_update_but_not_delete(private_client):
    cid = private_client.post(
        "/api/collections", json={"name": "Team drafts"}, headers=_as(ALICE)
    ).json()["id"]
    token = private_client.post(
        f"/api/collections/{cid}/share", json={"permission": "readwrite"}, headers=_as(ALICE)
    ).json()["share_id"]
    private_client.post(f"/api/shares/{token}/accept", headers=_as(BOB))

    assert (
        private_client.put(
            f"/api/collections/{cid}", json={"description": "bob was here"}, headers=_as(BOB)
        ).status_code
        == 200
    )
    assert private_client.delete(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 403
