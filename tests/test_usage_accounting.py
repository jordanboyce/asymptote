"""Chat usage accounting + the daily token budget.

Covers the data layer (rows, aggregates, retention), the budget rules
(unlimited by default, shared anonymous row, fails open on DB errors), and
the HTTP contract: an over-budget /api/chat returns a real 429 with
Retry-After and the shared body shape.
"""

import pytest
from fastapi.testclient import TestClient

import config
from services.app_database import SQLiteBackend, app_db
from services import usage_service
from services.usage_service import check_daily_budget, record_chat_usage


@pytest.fixture()
def fresh_db(tmp_path):
    old_db_path = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    yield app_db
    app_db.db_path = old_db_path


@pytest.fixture()
def budget(fresh_db):
    """Set a 1,000-token daily budget for the test, then restore."""
    old = config.settings.chat_daily_token_budget
    config.settings.chat_daily_token_budget = 1000
    yield config.settings
    config.settings.chat_daily_token_budget = old


# ── Data layer ──────────────────────────────────────────────────────────────


def test_usage_rows_and_user_aggregate(fresh_db):
    record_chat_usage("alice@example.com", "col1", "anthropic", "claude-x",
                      input_tokens=100, output_tokens=50, tool_calls=3)
    record_chat_usage("alice@example.com", "col1", "anthropic", "answer-cache",
                      input_tokens=0, output_tokens=0, cache_hit=True)
    record_chat_usage("bob@example.com", "col2", "openai", "gpt-x",
                      input_tokens=10, output_tokens=5)

    alice = app_db.get_user_usage_since("alice@example.com", "2000-01-01")
    assert alice == {"turns": 2, "input_tokens": 100, "output_tokens": 50}

    by_user = {r["grouped_by"]: r for r in app_db.get_usage_summary("2000-01-01", "user")}
    assert by_user["alice@example.com"]["cache_hits"] == 1
    assert by_user["alice@example.com"]["tool_calls"] == 3
    assert by_user["bob@example.com"]["turns"] == 1

    by_day = app_db.get_usage_summary("2000-01-01", "day")
    assert len(by_day) == 1 and by_day[0]["turns"] == 3


def test_anonymous_callers_share_one_row(fresh_db):
    record_chat_usage(None, "default", "ollama", None, input_tokens=7, output_tokens=3)
    record_chat_usage(None, "default", "ollama", None, input_tokens=5, output_tokens=1)
    anon = app_db.get_user_usage_since(None, "2000-01-01")
    assert anon == {"turns": 2, "input_tokens": 12, "output_tokens": 4}
    # And they surface in the rollup as one visible identity
    by_user = {r["grouped_by"]: r for r in app_db.get_usage_summary("2000-01-01", "user")}
    assert by_user["anonymous"]["turns"] == 2


def test_recording_never_raises(fresh_db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(app_db, "add_chat_usage", boom)
    record_chat_usage("x@example.com", "c", "anthropic", None, 1, 1)  # must not raise


def test_retention_deletes_old_rows(fresh_db):
    record_chat_usage("old@example.com", "c", "anthropic", None, 1, 1)
    import sqlite3
    with sqlite3.connect(app_db.db_path) as conn:
        conn.execute("UPDATE chat_usage SET timestamp = '2000-01-01T00:00:00'")
        conn.commit()
    assert app_db.delete_old_chat_usage(days=30) == 1
    assert app_db.get_user_usage_since("old@example.com", "2000-01-01")["turns"] == 0


def test_search_history_carries_identity(fresh_db):
    app_db.add_search_history(query="q", top_k=5, results_count=1,
                              user_id="alice@example.com", collection_id="col1")
    rows = app_db.get_search_history()
    assert rows[0]["user_id"] == "alice@example.com"
    assert rows[0]["collection_id"] == "col1"


# ── Budget rules ────────────────────────────────────────────────────────────


def test_budget_disabled_by_default(fresh_db):
    assert config.settings.chat_daily_token_budget == 0
    assert check_daily_budget("anyone@example.com") is None


def test_budget_blocks_after_spend(budget):
    assert check_daily_budget("alice@example.com") is None
    record_chat_usage("alice@example.com", "c", "anthropic", None,
                      input_tokens=800, output_tokens=300)
    over = check_daily_budget("alice@example.com")
    assert over is not None
    assert over["retry_after_seconds"] >= 1
    assert "budget" in over["detail"].lower()
    # Someone else is unaffected
    assert check_daily_budget("bob@example.com") is None


def test_budget_fails_open_on_db_error(budget, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(app_db, "get_user_usage_since", boom)
    assert check_daily_budget("alice@example.com") is None


# ── HTTP contract ───────────────────────────────────────────────────────────


def test_chat_returns_429_when_over_budget(budget):
    import main
    client = TestClient(main.app)
    record_chat_usage(config.settings.default_user_id, "c", "anthropic", None,
                      input_tokens=1200, output_tokens=0)
    r = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hello"}], "provider": "anthropic"},
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    body = r.json()
    assert set(body) == {"detail", "retry_after_seconds"}
    assert int(r.headers["Retry-After"]) == body["retry_after_seconds"]

    # Streaming path shares the gate and the contract
    r2 = client.post(
        "/api/chat/stream",
        json={"messages": [{"role": "user", "content": "hello"}], "provider": "anthropic"},
    )
    assert r2.status_code == 429
    assert r2.json()["retry_after_seconds"] >= 1
