"""Share invitations via Resend: gating, payload, and failure surfacing."""

import json

import pytest

import config
import services.share_email as se


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_disabled_without_key(monkeypatch):
    monkeypatch.setattr(config.settings, "resend_api_key", "")
    assert not se.share_email_enabled()
    with pytest.raises(RuntimeError, match="not configured"):
        se.send_share_email(
            "a@b.c", share_token="t", collection_name="X",
            permission="read", shared_by="me", app_url="http://x",
        )


def test_disabled_in_offline_mode(monkeypatch):
    monkeypatch.setattr(config.settings, "resend_api_key", "re_key")
    monkeypatch.setattr(config.settings, "offline_mode", True)
    assert not se.share_email_enabled()


def test_payload_and_headers(monkeypatch):
    monkeypatch.setattr(config.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(config.settings, "resend_from", "Clio <a@example.com>")
    monkeypatch.setattr(config.settings, "offline_mode", False)

    captured = {}

    def fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp({"id": "email-123"})

    monkeypatch.setattr(se.urllib.request, "urlopen", fake_urlopen)

    se.send_share_email(
        "teammate@example.com",
        share_token="tok-abc-123",
        collection_name="HOA audit",
        permission="readwrite",
        shared_by="alice@example.com",
        app_url="https://clio.example.com/",
        expires_at="2026-09-30T00:00:00",
    )

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_test_key"
    body = captured["body"]
    assert body["to"] == ["teammate@example.com"]
    assert body["from"] == "Clio <a@example.com>"
    assert "HOA audit" in body["subject"] and "alice@example.com" in body["subject"]
    # Both the deep link and the raw token reach the recipient
    assert "https://clio.example.com/?share_token=tok-abc-123" in body["text"]
    assert "tok-abc-123" in body["html"]
    assert "read and write" in body["text"]
    assert "2026-09-30" in body["text"]


def test_resend_rejection_is_readable(monkeypatch):
    import io
    import urllib.error

    monkeypatch.setattr(config.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(config.settings, "offline_mode", False)

    def fake_urlopen(req, timeout=15):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {},
            io.BytesIO(json.dumps({"message": "domain not verified"}).encode()),
        )

    monkeypatch.setattr(se.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="domain not verified"):
        se.send_share_email(
            "a@b.c", share_token="t", collection_name="X",
            permission="read", shared_by="me", app_url="http://x",
        )
