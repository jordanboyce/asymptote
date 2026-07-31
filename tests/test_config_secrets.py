"""Tests that GET /api/config never hands back a stored credential.

The Settings tab loads the config, drops each value into an input, and posts
the whole set back on save — so masking is only safe if an echoed mask means
"leave unchanged". Both halves are covered here.
"""

import pytest

from services.config_manager import MASKED_SECRET, SECRET_CONFIG_FIELDS, config_manager

SECRET = "sk-test-not-a-real-key"


@pytest.fixture()
def stored_key(monkeypatch):
    """Put a known key in the config DB, restoring whatever was there before."""
    from services.app_database import app_db

    previous = app_db.get_all_config().get("vision_ocr_api_key", "")
    app_db.set_config("vision_ocr_api_key", SECRET)
    yield app_db
    app_db.set_config("vision_ocr_api_key", previous)


def test_secret_is_masked_in_config_payload(stored_key):
    config = config_manager.get_current_config()
    assert config["vision_ocr_api_key"] == MASKED_SECRET
    assert SECRET not in str(config)


def test_masked_field_reports_whether_a_key_is_set(stored_key):
    assert config_manager.get_current_config()["vision_ocr_api_key_set"] is True

    config_manager.update_config({"vision_ocr_api_key": ""})
    config = config_manager.get_current_config()
    assert config["vision_ocr_api_key_set"] is False
    assert config["vision_ocr_api_key"] == ""


def test_echoing_the_mask_back_preserves_the_stored_key(stored_key):
    """A plain save from the UI must not overwrite the key with asterisks."""
    config_manager.update_config(
        {"vision_ocr_provider": "anthropic", "vision_ocr_api_key": MASKED_SECRET}
    )
    assert stored_key.get_all_config()["vision_ocr_api_key"] == SECRET


def test_a_real_new_value_still_overwrites(stored_key):
    config_manager.update_config({"vision_ocr_api_key": "sk-rotated"})
    assert stored_key.get_all_config()["vision_ocr_api_key"] == "sk-rotated"


def test_every_declared_secret_field_is_masked(stored_key):
    """Guards the list itself: adding a field to SECRET_CONFIG_FIELDS is enough."""
    config = config_manager.get_current_config()
    for field in SECRET_CONFIG_FIELDS:
        assert field in config, f"{field} is not a config field"
        assert config[field] in ("", MASKED_SECRET)
        assert f"{field}_set" in config
