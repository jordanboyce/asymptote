"""Online registration: gating, validation, open vs approval mode, the seat
cap, the admin review endpoints, and the public paths' auth exemption."""

import importlib

import pytest
from fastapi.testclient import TestClient

import config
from config import settings as _import_time_settings
from services import registration
from services.app_database import SQLiteBackend, app_db
from services.registration import RegistrationError

ADMIN = "admin@example.com"
USER = "user@example.com"


@pytest.fixture()
def fresh_db(tmp_path):
    old = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    yield app_db
    app_db.db_path = old


class _FakeEdge:
    """Stands in for the Cloudflare policy: a set of admitted addresses."""

    def __init__(self, admitted=None, fail=False):
        self.admitted = set(admitted or [])
        self.fail = fail

    def admitted_emails(self):
        return sorted(self.admitted)

    def admit_email(self, email):
        if self.fail:
            raise RuntimeError("Cloudflare rejected PUT")
        new = email not in self.admitted
        self.admitted.add(email)
        return new


@pytest.fixture()
def edge(monkeypatch, fresh_db):
    fake = _FakeEdge()
    monkeypatch.setattr(config.settings, "cf_api_token", "tok")
    monkeypatch.setattr(config.settings, "cf_account_id", "acct")
    monkeypatch.setattr(config.settings, "cf_access_policy_id", "pol")
    monkeypatch.setattr(config.settings, "offline_mode", False)
    monkeypatch.setattr(config.settings, "admin_emails", ADMIN)
    monkeypatch.setattr(config.settings, "registration_mode", "approval")
    monkeypatch.setattr(config.settings, "registration_allowed_domains", "")
    monkeypatch.setattr(config.settings, "registration_max_seats", 50)
    monkeypatch.setattr(registration, "admitted_emails", fake.admitted_emails)
    monkeypatch.setattr(registration, "admit_email", fake.admit_email)
    return fake


# ── Gating ──────────────────────────────────────────────────────────────


def test_closed_without_edge_admission(monkeypatch, fresh_db):
    monkeypatch.setattr(config.settings, "registration_mode", "open")
    monkeypatch.setattr(config.settings, "cf_api_token", "")
    assert not registration.registration_enabled()
    assert registration.public_config()["mode"] == "off"
    with pytest.raises(RegistrationError, match="closed"):
        registration.submit("a@b.co")


def test_closed_when_mode_off(edge, monkeypatch):
    monkeypatch.setattr(config.settings, "registration_mode", "off")
    assert registration.public_config() == {
        "enabled": False, "mode": "off", "allowed_domains": [], "product": "Clio",
    }


# ── Validation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "nope", "a@b", "a b@c.com", "@x.com"])
def test_rejects_malformed_addresses(edge, bad):
    with pytest.raises(RegistrationError, match="valid email"):
        registration.submit(bad)


def test_domain_allowlist(edge, monkeypatch):
    monkeypatch.setattr(config.settings, "registration_allowed_domains", "Example.com, partner.org")
    assert registration.allowed_domains() == ["example.com", "partner.org"]
    with pytest.raises(RegistrationError, match="limited to addresses at example.com, partner.org"):
        registration.submit("outsider@gmail.com")
    assert registration.submit("someone@sub.example.com")["status"] == "pending"
    assert registration.submit("someone@partner.org")["status"] == "pending"


def test_already_admitted_says_sign_in(edge):
    edge.admitted.add("in@example.com")
    out = registration.submit("In@Example.com")
    assert out["status"] == "admitted"
    assert app_db.get_registration_request_by_email("in@example.com") is None


# ── Approval mode ───────────────────────────────────────────────────────


def test_approval_mode_queues_and_admin_approves(edge):
    out = registration.submit("new@example.com", name="New Person", organization="Acme", note="hi")
    assert out["status"] == "pending"
    rows = registration.list_requests("pending")
    assert len(rows) == 1 and rows[0]["email"] == "new@example.com"
    assert rows[0]["name"] == "New Person" and rows[0]["organization"] == "Acme"
    assert "new@example.com" not in edge.admitted

    approved = registration.approve(rows[0]["id"], ADMIN, note="ok")
    assert approved["status"] == "approved" and approved["decided_by"] == ADMIN
    assert "new@example.com" in edge.admitted
    assert registration.list_requests("pending") == []


def test_resubmit_while_pending_keeps_one_row(edge):
    registration.submit("dup@example.com", name="First")
    registration.submit("dup@example.com", name="Second")
    rows = registration.list_requests()
    assert len(rows) == 1
    assert rows[0]["attempts"] == 2 and rows[0]["name"] == "Second"


def test_denied_then_reopened(edge):
    row = registration.submit("d@example.com")
    rid = registration.list_requests()[0]["id"]
    denied = registration.deny(rid, ADMIN, note="not now")
    assert denied["status"] == "denied" and denied["decision_note"] == "not now"
    again = registration.submit("d@example.com")
    assert again["status"] == "pending"
    assert registration.list_requests("pending")[0]["id"] == rid


def test_approve_unknown_request(edge):
    with pytest.raises(KeyError):
        registration.approve("nope", ADMIN)


# ── Open mode ───────────────────────────────────────────────────────────


def test_open_mode_admits_immediately(edge, monkeypatch):
    monkeypatch.setattr(config.settings, "registration_mode", "open")
    out = registration.submit("fast@example.com")
    assert out["status"] == "approved"
    assert "fast@example.com" in edge.admitted
    row = app_db.get_registration_request_by_email("fast@example.com")
    assert row["status"] == "approved" and row["decided_by"] == "auto"


def test_open_mode_queues_when_seats_are_full(edge, monkeypatch):
    monkeypatch.setattr(config.settings, "registration_mode", "open")
    monkeypatch.setattr(config.settings, "registration_max_seats", 2)
    edge.admitted.update({"a@x.com", "b@x.com"})
    out = registration.submit("c@x.com")
    assert out["status"] == "pending"
    assert "c@x.com" not in edge.admitted


def test_open_mode_queues_when_edge_fails(edge, monkeypatch):
    monkeypatch.setattr(config.settings, "registration_mode", "open")
    edge.fail = True
    out = registration.submit("e@x.com")
    assert out["status"] == "pending"
    assert app_db.get_registration_request_by_email("e@x.com")["status"] == "pending"


# ── HTTP: public endpoints are reachable without identity ───────────────


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
def private_client(edge, monkeypatch):
    import services.access_jwt as access_jwt
    import main

    saved = []
    for s in _settings_objects():
        saved.append((s, s.private_collections, s.cf_access_team_domain, s.cf_access_aud, s.admin_emails))
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


def test_public_paths_bypass_auth(private_client):
    assert private_client.get("/api/register/config").status_code == 200
    assert private_client.get("/api/user/me").status_code == 401  # everything else still gated
    r = private_client.post("/api/register", json={"email": "p@example.com", "name": "P"})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending"


def test_honeypot_is_silent(private_client):
    r = private_client.post("/api/register", json={"email": "bot@example.com", "website": "http://spam"})
    assert r.status_code == 202 and r.json()["status"] == "pending"
    assert app_db.get_registration_request_by_email("bot@example.com") is None


def test_invalid_address_is_400(private_client):
    r = private_client.post("/api/register", json={"email": "nope"})
    assert r.status_code == 400


def test_admin_endpoints_gated(private_client, edge):
    private_client.post("/api/register", json={"email": "q@example.com"})
    assert private_client.get("/api/access/registrations", headers=_as(USER)).status_code == 403
    r = private_client.get("/api/access/registrations", headers=_as(ADMIN))
    assert r.status_code == 200
    body = r.json()
    assert body["pending"] == 1 and body["mode"] == "approval"
    rid = body["requests"][0]["id"]
    assert private_client.post(f"/api/access/registrations/{rid}/approve", headers=_as(USER)).status_code == 403
    r = private_client.post(f"/api/access/registrations/{rid}/approve", json={"note": "welcome"}, headers=_as(ADMIN))
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert "q@example.com" in edge.admitted


def test_user_me_reports_registration_state(private_client, edge):
    private_client.post("/api/register", json={"email": "r@example.com"})
    me = private_client.get("/api/user/me", headers=_as(ADMIN)).json()
    assert me["registration_mode"] == "approval"
    assert me["pending_registrations"] == 1
    other = private_client.get("/api/user/me", headers=_as(USER)).json()
    assert other["pending_registrations"] == 0


def test_register_page_is_served(private_client):
    r = private_client.get("/register")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
