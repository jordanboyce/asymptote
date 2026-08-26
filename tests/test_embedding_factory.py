"""create_embedding_service routes to the provider settings call for.

The factory is the single provider-selection point shared by the indexer
manager and the reindex service — these tests pin that routing, the Ollama
Cloud key resolution chain, and the bearer-auth header on cloud calls.
"""

import json
from types import SimpleNamespace

import pytest

import services.embedder as embedder


def _settings():
    # Resolved at call time: test_auth.py reloads the config module mid-suite,
    # so a module-level `from config import settings` here would go stale and
    # monkeypatches would land on an object the factory no longer reads.
    import config
    return config.settings


@pytest.fixture()
def stub_services(monkeypatch):
    """Replace both service classes with recorders — no network, no models."""
    calls = {}

    class FakeLocal:
        def __init__(self, model_name):
            calls["local"] = {"model_name": model_name}

    class FakeOllama:
        def __init__(self, model_name, base_url, api_key=""):
            calls["ollama"] = {"model_name": model_name, "base_url": base_url, "api_key": api_key}

    monkeypatch.setattr(embedder, "EmbeddingService", FakeLocal)
    monkeypatch.setattr(embedder, "OllamaEmbeddingService", FakeOllama)
    return calls


def test_local_uses_global_model(monkeypatch, stub_services):
    monkeypatch.setattr(_settings(), "embedding_provider", "local")
    monkeypatch.setattr(_settings(), "embedding_model", "all-MiniLM-L6-v2")
    embedder.create_embedding_service()
    assert stub_services["local"] == {"model_name": "all-MiniLM-L6-v2"}


def test_local_collection_override_wins(monkeypatch, stub_services):
    monkeypatch.setattr(_settings(), "embedding_provider", "local")
    monkeypatch.setattr(_settings(), "embedding_model", "all-MiniLM-L6-v2")
    embedder.create_embedding_service(collection_model="BAAI/bge-base-en-v1.5")
    assert stub_services["local"] == {"model_name": "BAAI/bge-base-en-v1.5"}


def test_ollama_provider_uses_base_url_no_key(monkeypatch, stub_services):
    monkeypatch.setattr(_settings(), "embedding_provider", "ollama")
    monkeypatch.setattr(_settings(), "ollama_base_url", "http://box:11434")
    monkeypatch.setattr(_settings(), "ollama_embedding_model", "mxbai-embed-large")
    embedder.create_embedding_service()
    assert stub_services["ollama"] == {
        "model_name": "mxbai-embed-large",
        "base_url": "http://box:11434",
        "api_key": "",
    }


def test_ollama_cloud_uses_env_key(monkeypatch, stub_services):
    monkeypatch.setattr(_settings(), "embedding_provider", "ollama_cloud")
    monkeypatch.setattr(_settings(), "ollama_cloud_api_key", "env-key")
    monkeypatch.setattr(_settings(), "ollama_embedding_model", "nomic-embed-text")
    embedder.create_embedding_service()
    assert stub_services["ollama"] == {
        "model_name": "nomic-embed-text",
        "base_url": "https://ollama.com",
        "api_key": "env-key",
    }


def test_ollama_cloud_falls_back_to_team_key(monkeypatch, stub_services):
    from services.app_database import app_db

    monkeypatch.setattr(_settings(), "embedding_provider", "ollama_cloud")
    monkeypatch.setattr(_settings(), "ollama_cloud_api_key", "")
    monkeypatch.setattr(_settings(), "ollama_embedding_model", "nomic-embed-text")
    monkeypatch.setattr(
        app_db, "get_agent_api_key", lambda provider: "team-key" if provider == "ollama_cloud" else None
    )
    embedder.create_embedding_service()
    assert stub_services["ollama"]["api_key"] == "team-key"


def test_ollama_cloud_without_any_key_fails_closed(monkeypatch, stub_services):
    from services.app_database import app_db

    monkeypatch.setattr(_settings(), "embedding_provider", "ollama_cloud")
    monkeypatch.setattr(_settings(), "ollama_cloud_api_key", "")
    monkeypatch.setattr(app_db, "get_agent_api_key", lambda provider: None)
    with pytest.raises(RuntimeError, match="OLLAMA_CLOUD_API_KEY"):
        embedder.create_embedding_service()


def test_cloud_call_sends_bearer_header(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"embeddings": [[0.1, 0.2]]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr(embedder.urllib.request, "urlopen", fake_urlopen)
    svc = embedder.OllamaEmbeddingService(
        model_name="nomic-embed-text", base_url="https://ollama.com", api_key="sk-123"
    )
    assert captured["url"] == "https://ollama.com/api/embed"
    assert captured["auth"] == "Bearer sk-123"
    assert svc.embedding_dim == 2


def test_local_daemon_call_sends_no_auth_header(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"embeddings": [[0.1, 0.2, 0.3]]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr(embedder.urllib.request, "urlopen", fake_urlopen)
    embedder.OllamaEmbeddingService(model_name="nomic-embed-text")
    assert captured["auth"] is None
