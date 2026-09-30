"""Edge admission: gating, idempotency, rule preservation, and the safety rails."""

import json

import pytest

import config
import services.access_provisioning as ap


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _configure(monkeypatch, admins="boss@x.com"):
    monkeypatch.setattr(config.settings, "cf_api_token", "cf_tok")
    monkeypatch.setattr(config.settings, "cf_account_id", "acct123")
    monkeypatch.setattr(config.settings, "cf_access_policy_id", "pol456")
    monkeypatch.setattr(config.settings, "admin_emails", admins)
    monkeypatch.setattr(config.settings, "offline_mode", False)


def _fake_cf(monkeypatch, policy, captured):
    """Serve GET from `policy`, record the PUT/POST body in `captured`."""
    def fake_urlopen(req, timeout=15):
        captured["auth"] = req.get_header("Authorization")
        captured["url"] = req.full_url
        if req.get_method() == "GET":
            return _FakeResp({"success": True, "result": policy})
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp({"success": True, "result": {}})
    monkeypatch.setattr(ap.urllib.request, "urlopen", fake_urlopen)


# ── Gating ──────────────────────────────────────────────────────────────

def test_disabled_without_config(monkeypatch):
    monkeypatch.setattr(config.settings, "cf_api_token", "")
    assert not ap.access_provisioning_enabled()
    with pytest.raises(RuntimeError, match="not configured"):
        ap.admit_email("a@b.c")


def test_disabled_in_offline_mode(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config.settings, "offline_mode", True)
    assert not ap.access_provisioning_enabled()


def test_admin_gate(monkeypatch):
    _configure(monkeypatch, admins="Boss@X.com, other@x.com")
    assert ap.is_admin("boss@x.com")
    assert ap.is_admin("BOSS@x.com")      # identity casing varies by IdP
    assert ap.is_admin("other@x.com")
    assert not ap.is_admin("stranger@x.com")
    assert not ap.is_admin(None)          # anonymous password callers


def test_no_admins_configured_means_nobody(monkeypatch):
    """Fails closed: an empty allowlist must not read as 'everyone'."""
    _configure(monkeypatch, admins="")
    assert not ap.is_admin("boss@x.com")


# ── Admitting ───────────────────────────────────────────────────────────

def test_admit_appends_and_preserves_other_rules(monkeypatch):
    _configure(monkeypatch)
    policy = {
        "name": "clio-invited",
        "decision": "allow",
        "include": [
            {"email": {"email": "existing@x.com"}},
            {"email_domain": {"domain": "example.com"}},
        ],
        "require": [{"okta": {"name": "eng"}}],
    }
    captured = {}
    _fake_cf(monkeypatch, policy, captured)

    assert ap.admit_email("New@Guest.com") is True

    body = captured["body"]
    assert captured["method"] == "PUT"
    assert captured["auth"] == "Bearer cf_tok"
    assert body["name"] == "clio-invited"
    assert body["decision"] == "allow"
    # normalised, appended, and nothing else disturbed
    assert {"email": {"email": "new@guest.com"}} in body["include"]
    assert {"email_domain": {"domain": "example.com"}} in body["include"]
    assert {"email": {"email": "existing@x.com"}} in body["include"]
    assert body["require"] == [{"okta": {"name": "eng"}}]


def test_admit_is_idempotent(monkeypatch):
    _configure(monkeypatch)
    policy = {"name": "p", "decision": "allow",
              "include": [{"email": {"email": "dupe@x.com"}}]}
    captured = {}
    _fake_cf(monkeypatch, policy, captured)

    assert ap.admit_email("DUPE@x.com") is False
    assert "body" not in captured          # no write at all


def test_admit_refuses_past_cloudflare_cap(monkeypatch):
    _configure(monkeypatch)
    policy = {"name": "p", "decision": "allow",
              "include": [{"email": {"email": f"u{i}@x.com"}}
                          for i in range(ap.MAX_EMAILS_PER_RULE)]}
    _fake_cf(monkeypatch, policy, {})
    with pytest.raises(RuntimeError, match="per-rule cap"):
        ap.admit_email("one-too-many@x.com")


# ── Revoking ────────────────────────────────────────────────────────────

def test_revoke_removes_only_that_email(monkeypatch):
    _configure(monkeypatch)
    policy = {"name": "p", "decision": "allow", "include": [
        {"email": {"email": "keep@x.com"}},
        {"email": {"email": "drop@x.com"}},
        {"email_domain": {"domain": "example.com"}},
    ]}
    captured = {}
    _fake_cf(monkeypatch, policy, captured)

    assert ap.revoke_email("Drop@x.com") is True
    include = captured["body"]["include"]
    assert {"email": {"email": "drop@x.com"}} not in include
    assert {"email": {"email": "keep@x.com"}} in include
    assert {"email_domain": {"domain": "example.com"}} in include


def test_revoke_unknown_email_is_a_noop(monkeypatch):
    _configure(monkeypatch)
    policy = {"name": "p", "decision": "allow",
              "include": [{"email": {"email": "keep@x.com"}}]}
    captured = {}
    _fake_cf(monkeypatch, policy, captured)

    assert ap.revoke_email("never-there@x.com") is False
    assert "body" not in captured


def test_revoke_refuses_to_empty_the_policy(monkeypatch):
    """A policy with no Include rule locks everyone out, including the admin
    who would have to fix it."""
    _configure(monkeypatch)
    policy = {"name": "p", "decision": "allow",
              "include": [{"email": {"email": "last@x.com"}}]}
    captured = {}
    _fake_cf(monkeypatch, policy, captured)

    with pytest.raises(RuntimeError, match="only Include rule"):
        ap.revoke_email("last@x.com")
    assert "body" not in captured


# ── Error surfacing ─────────────────────────────────────────────────────

def test_api_error_is_readable(monkeypatch):
    _configure(monkeypatch)

    def fake_urlopen(req, timeout=15):
        return _FakeResp({"success": False, "errors": [{"message": "nope"}]})
    monkeypatch.setattr(ap.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="nope"):
        ap.admit_email("a@b.c")


# ── Batch admission ─────────────────────────────────────────────────────

def _fake_cf_counting(monkeypatch, policy, captured):
    """Like _fake_cf, but counts round-trips per method."""
    captured.setdefault("gets", 0)
    captured.setdefault("puts", 0)

    def fake_urlopen(req, timeout=15):
        if req.get_method() == "GET":
            captured["gets"] += 1
            return _FakeResp({"success": True, "result": policy})
        captured["puts"] += 1
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp({"success": True, "result": {}})
    monkeypatch.setattr(ap.urllib.request, "urlopen", fake_urlopen)


def test_batch_is_one_get_one_put(monkeypatch):
    """The whole point: N invitations must not be N read-modify-writes."""
    _configure(monkeypatch)
    policy = {"name": "p", "decision": "allow",
              "include": [{"email": {"email": "existing@x.com"}}]}
    captured = {}
    _fake_cf_counting(monkeypatch, policy, captured)

    results = ap.admit_emails(["a@x.com", "b@x.com", "existing@x.com", "c@x.com"])

    assert captured["gets"] == 1
    assert captured["puts"] == 1
    assert results == {
        "a@x.com": "added", "b@x.com": "added",
        "existing@x.com": "already", "c@x.com": "added",
    }
    written = [r["email"]["email"] for r in captured["body"]["include"]]
    assert written == ["existing@x.com", "a@x.com", "b@x.com", "c@x.com"]


def test_batch_dedupes_and_normalizes(monkeypatch):
    _configure(monkeypatch)
    policy = {"name": "p", "decision": "allow", "include": []}
    captured = {}
    _fake_cf_counting(monkeypatch, policy, captured)

    results = ap.admit_emails(["  A@X.com ", "a@x.com", "not-an-email", ""])
    assert results["a@x.com"] == "added"
    assert results["not-an-email"].startswith("rejected:")
    assert captured["puts"] == 1
    assert [r["email"]["email"] for r in captured["body"]["include"]] == ["a@x.com"]


def test_batch_partial_success_at_the_cap(monkeypatch):
    """Addresses under the cap land; the overflow is refused per-address,
    and the ones that fit are still written."""
    _configure(monkeypatch)
    existing = [{"email": {"email": f"u{i}@x.com"}} for i in range(ap.MAX_EMAILS_PER_RULE - 1)]
    policy = {"name": "p", "decision": "allow", "include": existing}
    captured = {}
    _fake_cf_counting(monkeypatch, policy, captured)

    results = ap.admit_emails(["fits@x.com", "overflow@x.com"])
    assert results["fits@x.com"] == "added"
    assert results["overflow@x.com"].startswith("rejected:")
    assert captured["puts"] == 1
    written = [r["email"]["email"] for r in captured["body"]["include"]]
    assert "fits@x.com" in written and "overflow@x.com" not in written


def test_batch_no_write_when_nothing_new(monkeypatch):
    _configure(monkeypatch)
    policy = {"name": "p", "decision": "allow",
              "include": [{"email": {"email": "a@x.com"}}]}
    captured = {}
    _fake_cf_counting(monkeypatch, policy, captured)

    results = ap.admit_emails(["a@x.com"])
    assert results == {"a@x.com": "already"}
    assert captured["puts"] == 0  # nothing changed → no PUT


def test_single_admit_still_works_through_batch(monkeypatch):
    """admit_email is now a thin wrapper over admit_emails."""
    _configure(monkeypatch)
    policy = {"name": "p", "decision": "allow", "include": []}
    captured = {}
    _fake_cf_counting(monkeypatch, policy, captured)
    assert ap.admit_email("new@x.com") is True
    with pytest.raises(RuntimeError, match="No email address"):
        ap.admit_email("")
