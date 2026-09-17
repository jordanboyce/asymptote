"""Shared test environment guards.

The developer/production .env next to the repo can carry live deployment
posture (PRIVATE_COLLECTIONS=true, AUTH_PASSWORD, CF_ACCESS_*). Tests must
run against the shipped defaults regardless, so the suite points Settings
at an env file that does not exist: every `importlib.reload(config)` then
reads real env vars and defaults only. Pinning individual variables is not
enough on its own — a fixture that `monkeypatch.delenv`s AUTH_PASSWORD
before its restoring reload would otherwise pull the real password back
out of .env, and every later test using `main.app` would answer 401.
Tests that want a mode on set it explicitly (fixture or
monkeypatch.setenv), which still wins.
"""

import os
import pathlib

os.environ["CLIO_ENV_FILE"] = str(
    pathlib.Path(__file__).resolve().parent / ".env.does-not-exist"
)
os.environ["PRIVATE_COLLECTIONS"] = "false"
os.environ["AUTH_PASSWORD"] = ""
# A deployment default LLM (an on-prem operator's private endpoint) would
# otherwise leak into every provider-construction test through the factory.
os.environ["AI_PROVIDER"] = ""
os.environ["AI_BASE_URL"] = ""
os.environ["AI_MODEL"] = ""
os.environ["AI_API_KEY"] = ""

# The rate limiter is always-on middleware with process-wide buckets; left
# enabled, hundreds of suite requests from one TestClient host would trip
# 429s in unrelated tests. Rate-limit tests re-enable it explicitly.
os.environ["RATE_LIMIT_ENABLED"] = "false"
