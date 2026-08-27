"""Semantic answer cache: similarity lookup, invalidation, and chat wiring."""

from types import SimpleNamespace

import numpy as np
import pytest

import config
import services.answer_cache as ac_module
from services.answer_cache import AnswerCache


V_A = np.array([1.0, 0.0, 0.0], dtype=np.float32)
V_A_CLOSE = np.array([0.98, 0.1, 0.0], dtype=np.float32)   # cos ≈ 0.995
V_B = np.array([0.0, 1.0, 0.0], dtype=np.float32)          # cos = 0


@pytest.fixture()
def cache(tmp_path):
    return AnswerCache(db_path=tmp_path / "cache.db")


def _store(cache, vec=V_A, scope="col:default", model="mini", question="what is x?",
           answer="x is 42", fingerprints=None):
    return cache.store(
        scope, model, question, vec, answer,
        sources=[{"filename": "a.pdf", "page_number": 1, "text_snippet": "x",
                  "similarity_score": 0.9, "document_id": "doc1", "collection_id": "default"}],
        fingerprints=fingerprints if fingerprints is not None else [
            {"collection_id": "default", "document_id": "doc1",
             "filename": "a.pdf", "upload_timestamp": "t1", "num_chunks": 3}
        ],
        provider="ollama",
    )


def test_hit_above_threshold(cache):
    _store(cache)
    entry = cache.find_best("col:default", "mini", V_A_CLOSE, threshold=0.9)
    assert entry is not None
    assert entry["answer"] == "x is 42"
    assert entry["similarity"] >= 0.99


def test_miss_below_threshold_and_wrong_scope(cache):
    _store(cache)
    assert cache.find_best("col:default", "mini", V_B, threshold=0.9) is None
    assert cache.find_best("col:other", "mini", V_A, threshold=0.9) is None
    assert cache.find_best("col:default", "other-model", V_A, threshold=0.9) is None


def test_near_duplicate_store_replaces(cache):
    _store(cache, answer="old answer")
    _store(cache, vec=V_A, answer="new answer")
    entry = cache.find_best("col:default", "mini", V_A, threshold=0.9)
    assert entry["answer"] == "new answer"
    assert cache.stats()["entries"] == 1


def test_lru_cap_evicts_oldest(cache, monkeypatch):
    monkeypatch.setattr(config.settings, "answer_cache_max_per_scope", 2)
    _store(cache, vec=V_A, question="q1")
    _store(cache, vec=V_B, question="q2")
    v3 = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    _store(cache, vec=v3, question="q3")
    assert cache.stats()["entries"] == 2
    assert cache.find_best("col:default", "mini", v3, threshold=0.9) is not None


def test_clear(cache):
    _store(cache)
    assert cache.clear() == 1
    assert cache.stats()["entries"] == 0


# ── Chat-layer wiring ───────────────────────────────────────────────────────


class _FakeEmbedder:
    model_name = "mini"

    def embed_query(self, q):
        return V_A


@pytest.fixture()
def chat_env(cache, monkeypatch):
    """Point the chat helpers at a temp cache and a fake local embedder."""
    import api.chat as chat

    monkeypatch.setattr(ac_module, "answer_cache", cache)
    monkeypatch.setattr(
        chat, "get_indexer", lambda cid="default": SimpleNamespace(embedding_service=_FakeEmbedder())
    )
    monkeypatch.setattr(config.settings, "enable_answer_cache", True)
    monkeypatch.setattr(config.settings, "answer_cache_threshold", 0.9)
    return chat


def _request(messages=None, scope="current", use_cache=True):
    from models.schemas import ChatMessage, ChatRequest

    return ChatRequest(
        messages=messages or [ChatMessage(role="user", content="what is x?")],
        provider="ollama",
        scope=scope,
        use_cache=use_cache,
    )


def test_cache_context_single_turn_only(chat_env):
    from models.schemas import ChatMessage

    ctx = chat_env._cache_context(_request(), "default")
    assert ctx and ctx["scope_key"] == "col:default" and ctx["embedding_model"] == "mini"

    multi = _request(messages=[
        ChatMessage(role="user", content="what is x?"),
        ChatMessage(role="assistant", content="42"),
        ChatMessage(role="user", content="why?"),
    ])
    assert chat_env._cache_context(multi, "default") is None


def test_cache_context_disabled(chat_env, monkeypatch):
    monkeypatch.setattr(config.settings, "enable_answer_cache", False)
    assert chat_env._cache_context(_request(), "default") is None


def test_lookup_serves_fresh_entry(chat_env, cache, monkeypatch):
    _store(cache)
    monkeypatch.setattr(
        chat_env, "_document_fingerprint",
        lambda cid, did: {"filename": "a.pdf", "upload_timestamp": "t1", "num_chunks": 3},
    )
    ctx = chat_env._cache_context(_request(), "default")
    entry = chat_env._cache_lookup(ctx)
    assert entry is not None and entry["answer"] == "x is 42"


def test_lookup_discards_entry_when_source_changed(chat_env, cache, monkeypatch):
    _store(cache)
    # Document re-uploaded since the answer was generated
    monkeypatch.setattr(
        chat_env, "_document_fingerprint",
        lambda cid, did: {"filename": "a.pdf", "upload_timestamp": "t2-CHANGED", "num_chunks": 3},
    )
    ctx = chat_env._cache_context(_request(), "default")
    assert chat_env._cache_lookup(ctx) is None
    assert cache.stats()["entries"] == 0  # stale entry was deleted, not just skipped


def test_lookup_discards_entry_when_source_deleted(chat_env, cache, monkeypatch):
    _store(cache)
    monkeypatch.setattr(chat_env, "_document_fingerprint", lambda cid, did: None)
    ctx = chat_env._cache_context(_request(), "default")
    assert chat_env._cache_lookup(ctx) is None


def test_store_skips_sourceless_answers(chat_env, cache):
    ctx = chat_env._cache_context(_request(), "default")
    chat_env._cache_store(ctx, "no idea", [], "ollama")
    assert cache.stats()["entries"] == 0


def test_store_then_lookup_roundtrip(chat_env, cache, monkeypatch):
    fp = {"filename": "b.pdf", "upload_timestamp": "t9", "num_chunks": 7}
    monkeypatch.setattr(chat_env, "_document_fingerprint", lambda cid, did: dict(fp))
    result = SimpleNamespace(
        filename="b.pdf", page_number=2, text_snippet="snippet",
        similarity_score=0.8, document_id="doc9",
    )
    ctx = chat_env._cache_context(_request(), "default")
    chat_env._cache_store(ctx, "the answer", [(result, "default")], "ollama")

    entry = chat_env._cache_lookup(ctx)
    assert entry["answer"] == "the answer"
    assert entry["sources"][0]["collection_id"] == "default"
    # URL rebuild uses the current request's host
    payload = chat_env._cached_sources(entry, "http://x")
    assert payload[0]["pdf_url"].startswith("http://x/documents/doc9/pdf?collection_id=default")
