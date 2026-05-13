"""Operator-provisioned beta API keys.

Two configuration sources, file or env var:

- ``settings.beta_keys_yaml`` — inline YAML content (env var BETA_KEYS_YAML).
  Designed for hosted deployments like Railway where the file can't be
  committed. Cached for process lifetime; edits require a redeploy.
- ``settings.beta_keys_file`` — path to a YAML file on disk. Designed for
  local dev. Cached by mtime; edits hot-reload without restart.

When either is set, the ``get_current_user_id`` middleware calls
:func:`ensure_seeded` once per process per user to populate the
``user_api_keys`` table. ``beta_keys_yaml`` takes precedence when both are set.

YAML schema (top-level keys are user emails, case-insensitive)::

    jordan.boyce@cyberlion.dev:
      anthropic: sk-ant-...
      google: AIza...
      ollama_cloud: ...

Seeding is INSERT-ONLY — an existing row is never overwritten. To rotate a
key, delete the row (or the whole user) and let the next request re-seed.
Designed for the closed-beta phase where the operator owns all keys; do not
expose the YAML path or its contents through any API.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, Set

import yaml

from config import settings
from services.app_database import app_db

logger = logging.getLogger(__name__)

# Per-process memo so a user's seed check is paid once per restart.
_seeded_users: Set[str] = set()
_cache_lock = threading.Lock()

# Cached YAML payload + mtime so we re-read the file when the operator edits it.
_yaml_cache: Dict[str, Dict[str, str]] = {}
_yaml_mtime: float = 0.0


def _parse_yaml_text(raw_text: str, source: str) -> Dict[str, Dict[str, str]]:
    """Parse YAML text and normalize to ``{email_lower: {provider_lower: key}}``.

    Returns an empty dict on any parse error (logged, never raised — a
    malformed config must not break login). ``source`` is used in log messages
    only.
    """
    try:
        raw = yaml.safe_load(raw_text) or {}
    except Exception as exc:
        logger.warning("Failed to parse beta keys from %s: %s", source, exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("Beta keys from %s: top-level must be a mapping; got %s", source, type(raw).__name__)
        return {}

    normalized: Dict[str, Dict[str, str]] = {}
    for email, providers in raw.items():
        if not isinstance(providers, dict):
            continue
        entries: Dict[str, str] = {}
        for provider, key in providers.items():
            if isinstance(provider, str) and isinstance(key, str) and key.strip():
                entries[provider.strip().lower()] = key.strip()
        if entries:
            normalized[str(email).strip().lower()] = entries
    return normalized


def _resolve_beta_keys() -> Dict[str, Dict[str, str]]:
    """Load the beta-keys mapping from env var content or file path.

    ``settings.beta_keys_yaml`` (env BETA_KEYS_YAML) takes precedence and is
    cached for the process lifetime — edits require a redeploy. Falling back
    to ``settings.beta_keys_file`` re-reads on mtime change so local-dev edits
    hot-reload without restart. Returns ``{}`` when neither is configured.
    """
    global _yaml_cache, _yaml_mtime

    # Inline content path (Railway / hosted deployments)
    if settings.beta_keys_yaml:
        with _cache_lock:
            if _yaml_cache and _yaml_mtime < 0:
                return _yaml_cache
            _yaml_cache = _parse_yaml_text(settings.beta_keys_yaml, "BETA_KEYS_YAML env")
            _yaml_mtime = -1.0  # sentinel: inline content, never reloaded
            _seeded_users.clear()
            return _yaml_cache

    # File path (local dev)
    if not settings.beta_keys_file:
        return {}
    path = Path(settings.beta_keys_file)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}

    with _cache_lock:
        if _yaml_cache and mtime == _yaml_mtime:
            return _yaml_cache
        try:
            raw_text = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read beta_keys_file %s: %s", path, exc)
            return {}
        _yaml_cache = _parse_yaml_text(raw_text, f"file {path}")
        _yaml_mtime = mtime
        _seeded_users.clear()
        return _yaml_cache


def ensure_seeded(user_id: str) -> None:
    """Populate ``user_api_keys`` rows for ``user_id`` from the beta config.

    Safe to call on every request — work is skipped after the first hit per
    process. Existing rows are preserved; only missing (user, provider) pairs
    are inserted.
    """
    if not (settings.beta_keys_yaml or settings.beta_keys_file):
        return
    if user_id in _seeded_users:
        return

    data = _resolve_beta_keys()
    providers = data.get(user_id.lower())

    with _cache_lock:
        _seeded_users.add(user_id)

    if not providers:
        return

    for provider, api_key in providers.items():
        try:
            if app_db.user_api_key_exists(user_id, provider):
                continue
            app_db.set_user_api_key(user_id, provider, api_key)
            logger.info("Seeded beta API key for %s / %s", user_id, provider)
        except Exception as exc:
            logger.warning("Failed to seed beta key for %s / %s: %s", user_id, provider, exc)


def list_seeded_providers_for(user_id: str) -> list[str]:
    """Return the provider names the beta config lists for this user (without keys).

    Used by the discovery endpoint so the frontend can render which providers
    are server-managed even before any request has hit the DB. Returns ``[]``
    when neither source is configured or no entry exists for the user.
    """
    if not (settings.beta_keys_yaml or settings.beta_keys_file):
        return []
    data = _resolve_beta_keys()
    return sorted(data.get(user_id.lower(), {}).keys())
