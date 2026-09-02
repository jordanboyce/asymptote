"""Admin visibility endpoints + the operator gates added with them.

Under private collections, /api/admin/* and the server-path probes
(scan-folder, pickers) are ADMIN_EMAILS-only and fail closed. With private
collections off they are deliberate no-ops — the shared-appliance trust
model, same as POST /api/config.

The HTTP harness mirrors tests/test_private_collections.py: settings
flipped on the live Settings objects, the Access verifier faked, main
reloaded so middleware registration matches the mode.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import config
from config import settings as _import_time_settings
from services.app_database import SQLiteBackend, app_db
from services.usage_service import record_chat_usage

ADMIN = "admin@example.com"
USER = "user@example.com"


@pytest.fixture()
def fresh_db(tmp_path):
    old_db_path = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    yield app_db
    app_db.db_path = old_db_path


def _settings_objects():
    objs = {id(_import_time_settings): _import_time_settings}
    objs.setdefault(id(config.settings), config.settings)
    return list(objs.values())


class _FakeVerifier:
    def verify(self, token):
        return {"email": token} if token and "@" in token else None

    @staticmethod
    def identity_from_claims(claims):
        return claims.get("email")


@pytest.fixture()
def private_admin_client(fresh_db, monkeypatch):
    import services.access_jwt as access_jwt
    import main

    saved = []
    for s in _settings_objects():
        saved.append((s, s.private_collections, s.cf_access_team_domain,
                      s.cf_access_aud, s.admin_emails))
        s.private_collections = True
        s.cf_access_team_domain = "testteam.cloudflareaccess.com"
        s.cf_access_aud = "aud-test"
        s.admin_emails = ADMIN
    monkeypatch.setattr(access_jwt, "get_verifier", lambda: _FakeVerifier())
    reloaded = importlib.reload(main)
    yield TestClient(reloaded.app)
    for s, private, team, aud, admins in saved:
        s.private_collections = private
        s.cf_access_team_domain = team
        s.cf_access_aud = aud
        s.admin_emails = admins
    importlib.reload(main)


def _as(email):
    return {"cf-access-jwt-assertion": email}


# ── Private mode: fails closed ──────────────────────────────────────────────


def test_admin_endpoints_reject_non_admin(private_admin_client):
    for path in ("/api/admin/usage", "/api/admin/search-history", "/api/admin/stats"):
        r = private_admin_client.get(path, headers=_as(USER))
        assert r.status_code == 403, path


def test_admin_endpoints_allow_admin(private_admin_client):
    record_chat_usage(USER, "col1", "anthropic", "claude-x",
                      input_tokens=10, output_tokens=5)

    r = private_admin_client.get("/api/admin/usage", headers=_as(ADMIN))
    assert r.status_code == 200
    body = r.json()
    assert {"days", "by_user", "by_day", "daily_token_budget"} <= set(body)
    assert any(row["grouped_by"] == USER for row in body["by_user"])

    r = private_admin_client.get("/api/admin/stats", headers=_as(ADMIN))
    assert r.status_code == 200
    stats = r.json()
    for key in ("in_flight", "requests_total", "errors_total", "uptime_seconds",
                "rate_limit", "index_jobs_active", "usage_today"):
        assert key in stats, key
    assert stats["in_flight"] >= 1  # this request itself

    r = private_admin_client.get("/api/admin/search-history", headers=_as(ADMIN))
    assert r.status_code == 200
    assert "searches" in r.json()


def test_path_probes_are_admin_only(private_admin_client):
    r = private_admin_client.post("/api/scan-folder", json={"path": "C:/"},
                                  headers=_as(USER))
    assert r.status_code == 403
    r = private_admin_client.post("/api/folder-picker", headers=_as(USER))
    assert r.status_code == 403
    r = private_admin_client.post("/api/file-picker", headers=_as(USER))
    assert r.status_code == 403


# ── Open mode: deliberate no-op ─────────────────────────────────────────────


def test_open_mode_admin_endpoints_are_open(fresh_db):
    import main
    client = TestClient(main.app)
    assert client.get("/api/admin/stats").status_code == 200
    assert client.get("/api/admin/usage").status_code == 200


# ── Global index-job cap ────────────────────────────────────────────────────


def test_global_job_cap_blocks_at_limit(fresh_db):
    import threading
    from services.upload_service import upload_service

    old = config.settings.max_concurrent_index_jobs
    config.settings.max_concurrent_index_jobs = 2
    try:
        upload_service._check_global_job_cap()  # nothing running → fine
        with upload_service._lock:
            upload_service._active_threads[9001] = threading.Thread()
            upload_service._active_threads[9002] = threading.Thread()
        with pytest.raises(RuntimeError, match="limit 2"):
            upload_service._check_global_job_cap()
        config.settings.max_concurrent_index_jobs = 0  # 0 = uncapped
        upload_service._check_global_job_cap()
    finally:
        config.settings.max_concurrent_index_jobs = old
        with upload_service._lock:
            upload_service._active_threads.pop(9001, None)
            upload_service._active_threads.pop(9002, None)
