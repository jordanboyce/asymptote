"""Provider model discovery + Ollama Cloud validation.

Covers the failure mode where an advisor could not configure an Ollama Cloud
model at all:

* `POST /api/providers/models` called `create_provider(provider_id=...)` against
  a factory whose parameter is `provider_name`, so every Refresh 500'd;
* the frontend swallowed that error and silently fell back to a built-in list;
* `OllamaCloudProvider.validate()` probed a hard-coded `gpt-oss:20b` rather than
  the model the advisor had selected — and saving the config is gated on
  validation passing.

Everything here is offline: the OpenAI client is stubbed.
"""

import asyncio

import pytest
from fastapi import HTTPException

import main
from services.ai_service import (
    ModelNotAvailableError,
    OllamaCloudProvider,
    create_provider,
)


# ── Stubs ────────────────────────────────────────────────────────────────


class _Model:
    def __init__(self, mid):
        self.id = mid


class _Page:
    def __init__(self, ids):
        self.data = [_Model(m) for m in ids]


class _StubClient:
    """Minimal stand-in for the OpenAI SDK client surface we use."""

    def __init__(self, model_ids=(), on_completion=None):
        # An Exception here means "the /models call fails", not "these models".
        self._model_ids = model_ids if isinstance(model_ids, Exception) else list(model_ids)
        self._on_completion = on_completion
        self.completion_calls = []
        outer = self

        class _Models:
            def list(self):
                if isinstance(outer._model_ids, Exception):
                    raise outer._model_ids
                return _Page(outer._model_ids)

        class _Completions:
            def create(self, **kwargs):
                outer.completion_calls.append(kwargs)
                if outer._on_completion:
                    return outer._on_completion(**kwargs)
                return object()

        class _Chat:
            completions = _Completions()

        self.models = _Models()
        self.chat = _Chat()


def _provider(model=None, **stub_kwargs):
    p = OllamaCloudProvider.__new__(OllamaCloudProvider)
    p._base_url = "https://ollama.com/v1"
    p.client = _StubClient(**stub_kwargs)
    if model:
        p.FAST_MODEL = model
        p.QUALITY_MODEL = model
    return p


# ── The factory contract the route broke ─────────────────────────────────


def test_create_provider_takes_provider_name_positionally():
    """The route used to pass `provider_id=`, which the factory doesn't accept."""
    with pytest.raises(TypeError):
        create_provider(provider_id="ollama_cloud", api_key="k")  # noqa

    p = create_provider("ollama_cloud", "k")
    assert isinstance(p, OllamaCloudProvider)


def test_ollama_cloud_defaults_to_gemma4():
    assert create_provider("ollama_cloud", "k").QUALITY_MODEL == "gemma4:31b"


def test_ollama_cloud_honors_selected_model():
    p = create_provider("ollama_cloud", "k", model="gpt-oss:120b")
    assert p.QUALITY_MODEL == "gpt-oss:120b"


# ── Validation probes the selected model ─────────────────────────────────


def test_validate_probes_the_configured_model_not_a_hardcoded_one():
    p = _provider(model="gemma4:31b")
    assert p.validate() is True
    assert p.client.completion_calls[0]["model"] == "gemma4:31b"


def test_validate_probes_the_default_when_no_model_selected():
    p = _provider()
    p.validate()
    assert p.client.completion_calls[0]["model"] == "gemma4:31b"


def test_unknown_model_is_not_reported_as_a_bad_key():
    """A working key + a wrong model tag must not read as "invalid key"."""
    from openai import NotFoundError

    def _raise(**_):
        raise NotFoundError(
            "model not found", response=_FakeResponse(404), body=None
        )

    p = _provider(model="no-such-model", on_completion=_raise)
    with pytest.raises(ModelNotAvailableError) as exc:
        p.validate()
    assert "no-such-model" in str(exc.value)


def test_bad_key_still_reports_invalid():
    from openai import AuthenticationError

    def _raise(**_):
        raise AuthenticationError(
            "bad key", response=_FakeResponse(401), body=None
        )

    assert _provider(on_completion=_raise).validate() is False


class _FakeResponse:
    """The openai SDK errors want a response object with a status_code."""

    def __init__(self, status):
        self.status_code = status
        self.headers = {}
        self.request = None


# ── Model listing ────────────────────────────────────────────────────────


def test_list_models_leads_with_preferred_models_and_labels_them():
    p = _provider(model_ids=["zeta:1b", "gpt-oss:20b", "gemma4:31b", "alpha:2b"])
    ids = [m["id"] for m in p.list_models()]
    assert ids[:2] == ["gemma4:31b", "gpt-oss:20b"]
    assert ids[2:] == ["alpha:2b", "zeta:1b"]  # remainder sorted, not dropped

    labels = {m["id"]: m["label"] for m in p.list_models()}
    assert "Gemma 4 31B" in labels["gemma4:31b"]
    assert labels["alpha:2b"] == "alpha:2b"  # unknown tags still selectable


# ── Free-tier ordering ───────────────────────────────────────────────────
#
# Most Finn users are on a *free* Ollama account, so the picker must be
# ordered by published usage tier — not by parameter count, which would put
# the models that drain an allowance fastest at the top.


def test_cheapest_tiers_come_first():
    p = _provider(model_ids=[
        "kimi-k3",            # metered — bills per token
        "deepseek-v4-pro",    # Extra High
        "glm-5.2",            # High
        "gpt-oss:120b",       # Medium
        "gemma4:31b",         # Low
    ])
    assert [m["id"] for m in p.list_models()] == [
        "gemma4:31b", "gpt-oss:120b", "glm-5.2", "deepseek-v4-pro", "kimi-k3",
    ]


def test_the_default_is_the_recommended_low_tier_model():
    assert OllamaCloudProvider.QUALITY_MODEL == "gemma4:31b"
    tier, context, modalities = OllamaCloudProvider.USAGE_TIERS["gemma4:31b"]
    assert tier == "Low"
    assert "vision" in modalities
    label = OllamaCloudProvider.describe_model("gemma4:31b")
    assert "Low usage" in label and "recommended" in label


def test_every_preferred_model_is_low_usage_and_tool_capable():
    """Leading the picker with a model that drains a free key is the bug."""
    from services.ai_service import KNOWN_MODELS

    for mid in OllamaCloudProvider.PREFERRED_MODELS:
        assert OllamaCloudProvider.USAGE_TIERS[mid][0] == "Low", mid
        assert KNOWN_MODELS[mid]["tools"] is True, mid


def test_metered_models_are_labelled_as_billing_per_token():
    assert "billed per token" in OllamaCloudProvider.describe_model("kimi-k3")
    assert "billed per token" not in OllamaCloudProvider.describe_model("gemma4:31b")


def test_untiered_models_sort_above_metered_but_below_known_tiers():
    """An unknown tag is unknown — not assumed cheap, not assumed expensive."""
    p = _provider(model_ids=["kimi-k3", "brand-new:9b", "deepseek-v4-pro", "gemma4:31b"])
    assert [m["id"] for m in p.list_models()] == [
        "gemma4:31b", "deepseek-v4-pro", "brand-new:9b", "kimi-k3",
    ]


def test_usage_tiers_and_frontend_fallback_agree():
    """The Vue picker hard-codes a fallback list; it must not drift from here."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "frontend/src/utils/aiProviders.js"
    block = re.search(
        r"id: 'ollama_cloud'.*?models: \[(.*?)\]", src.read_text(encoding="utf-8"), re.S
    )
    assert block, "could not locate the ollama_cloud model list"
    entries = re.findall(r"\{ id: '([^']+)', label: '([^']+)' \}", block.group(1))
    assert entries, "no models parsed out of the fallback list"

    for mid, label in entries:
        assert mid in OllamaCloudProvider.USAGE_TIERS, f"{mid} has no known usage tier"
        tier = OllamaCloudProvider.USAGE_TIERS[mid][0]
        assert tier in ("Low", "Medium"), f"{mid} is {tier} — too costly to suggest by default"
        assert f"{tier} usage" in label, f"{mid} label disagrees with its tier: {label}"

    assert entries[0][0] == "gemma4:31b", "the free-tier pick must be listed first"


def test_list_models_raises_instead_of_returning_empty_on_failure():
    """An empty list means "no models"; a failure must not masquerade as one."""
    p = _provider(model_ids=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        p.list_models()


# ── The route ────────────────────────────────────────────────────────────


def test_discover_models_returns_the_provider_list(monkeypatch):
    stub = _provider(model_ids=["gemma4:31b", "gpt-oss:20b"])
    monkeypatch.setattr(main, "create_provider", lambda *a, **k: stub)

    req = main.ModelDiscoveryRequest(provider_id="ollama_cloud", api_key="k")
    assert [m["id"] for m in asyncio.run(main.discover_models(req))] == [
        "gemma4:31b",
        "gpt-oss:20b",
    ]


def test_discover_models_rejects_unknown_provider():
    req = main.ModelDiscoveryRequest(provider_id="not-a-provider", api_key="k")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.discover_models(req))
    assert exc.value.status_code == 400


def test_discover_models_reports_a_missing_key_as_a_client_error():
    req = main.ModelDiscoveryRequest(provider_id="ollama_cloud", api_key=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.discover_models(req))
    assert exc.value.status_code == 400
    assert "API key required" in exc.value.detail


def test_discover_models_surfaces_provider_failures(monkeypatch):
    stub = _provider(model_ids=RuntimeError("upstream is down"))
    monkeypatch.setattr(main, "create_provider", lambda *a, **k: stub)

    req = main.ModelDiscoveryRequest(provider_id="ollama_cloud", api_key="k")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.discover_models(req))
    assert exc.value.status_code == 502
    assert "upstream is down" in exc.value.detail
