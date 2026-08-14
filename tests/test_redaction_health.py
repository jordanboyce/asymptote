"""`/health` must report whether the PII boundary is actually enforcing.

A deployment can lose redaction without losing anything else. Two ways:

  * Presidio is not importable — ``redaction_engine.available`` is False and
    ``redact_text_for_ai`` returns the input unchanged. Silent pass-through.
  * Presidio imports but ``en_core_web_lg`` was never downloaded — the engine
    raises on the first real redaction, mid-conversation. The image builds and
    starts cleanly, so nothing catches it before a user does.

Either way the app reports healthy while the guarantee it sells is off. The
``enforcing`` field is the one a deploy check should assert.
"""

from __future__ import annotations

import main


def test_reports_enforcing_when_everything_is_wired():
    health = main._redaction_health()
    assert health["enabled"] is True
    assert health["engine_available"] is True
    assert health["model_ready"] is True
    assert health["enforcing"] is True


def test_missing_spacy_model_is_not_enforcing(monkeypatch):
    """The container-built-without-the-model case."""
    from services.privacy import redaction_engine as re_mod

    def _boom(*a, **kw):
        raise OSError("[E050] Can't find model 'en_core_web_lg'.")

    monkeypatch.setattr(re_mod.redaction_engine, "redact_text", _boom)

    health = main._redaction_health()
    assert health["engine_available"] is True
    assert health["model_ready"] is False
    assert health["enforcing"] is False
    assert "E050" in health.get("detail", "")


def test_unavailable_engine_is_not_enforcing(monkeypatch):
    """The silent pass-through case — the more dangerous of the two."""
    from services.privacy import redaction_engine as re_mod

    monkeypatch.setattr(
        type(re_mod.redaction_engine), "available", property(lambda self: False)
    )

    health = main._redaction_health()
    assert health["engine_available"] is False
    assert health["enforcing"] is False


def test_redaction_switched_off_is_not_enforcing(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "enable_pii_redaction", False)

    health = main._redaction_health()
    assert health["enabled"] is False
    assert health["enforcing"] is False


def test_health_endpoint_carries_the_block():
    import asyncio

    payload = asyncio.run(main.health())
    assert "redaction" in payload
    assert "enforcing" in payload["redaction"]
