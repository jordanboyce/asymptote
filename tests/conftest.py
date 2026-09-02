"""Shared test environment guards.

The developer/production .env next to the repo can carry live deployment
posture (PRIVATE_COLLECTIONS=true, AUTH_PASSWORD, CF_ACCESS_*). Tests must
run against the shipped defaults regardless — real env vars outrank the
.env file in pydantic-settings, so forcing them here (before any test module
imports config) pins the baseline. Tests that want a mode on set it
explicitly (fixture or monkeypatch.setenv), which still wins.
"""

import os

os.environ["PRIVATE_COLLECTIONS"] = "false"
os.environ["AUTH_PASSWORD"] = ""

# The rate limiter is always-on middleware with process-wide buckets; left
# enabled, hundreds of suite requests from one TestClient host would trip
# 429s in unrelated tests. Rate-limit tests re-enable it explicitly.
os.environ["RATE_LIMIT_ENABLED"] = "false"
