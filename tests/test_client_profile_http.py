"""HTTP surface for the client profile and meeting prep routes.

These are the first routes to live in `api/` rather than `main.py`, so this
module also pins the wiring: the routers are mounted, and they resolve
`get_indexer` / `require_collection_access` through `api.deps`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.financial.client_profile import ClientProfileStore


class _StubVectorStore:
    def __init__(self, profile_store, holdings_store=None, notes_store=None, metadata_store=None):
        self.client_profile_store = profile_store
        self.holdings_store = holdings_store
        self.meeting_notes_store = notes_store
        self.metadata_store = metadata_store


class _StubIndexer:
    def __init__(self, vector_store):
        self.vector_store = vector_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    import api.client_profile as cp_routes
    import api.meeting_prep as prep_routes
    import main

    store = ClientProfileStore(tmp_path / "metadata.db")
    indexer = _StubIndexer(_StubVectorStore(store))

    for module in (cp_routes, prep_routes):
        monkeypatch.setattr(module, "get_indexer", lambda cid, _i=indexer: _i)
        monkeypatch.setattr(module, "require_collection_access", lambda *a, **k: "owner")

    return TestClient(main.app)


PROFILE = {
    "display_name": "Henderson Household",
    "risk_tolerance": "moderate",
    "time_horizon_years": 12,
    "household_members": [{"name": "Dana Henderson", "relationship": "primary"}],
    "goals": [{"label": "Retirement", "target_amount": 2500000}],
    "ips": {
        "allocation_targets": [
            {"asset_class": "Equities", "target_pct": 60},
            {"asset_class": "Fixed Income", "target_pct": 40},
        ],
        "rebalance_band_pct": 5,
        "max_single_position_pct": 8,
        "prohibited_holdings": ["XOM"],
    },
    "tax": {"federal_bracket_pct": 32, "filing_status": "married_joint"},
    "liquidity": {"cash_reserve_target": 150000},
}


def test_get_returns_empty_profile_rather_than_404(client):
    """The form needs a shape to bind to before anything is saved."""
    r = client.get("/api/collections/c1/profile")
    assert r.status_code == 200
    body = r.json()

    assert body["collection_id"] == "c1"
    assert body["exists"] is False
    assert body["profile"]["ips"]["allocation_targets"] == []
    assert body["completeness"]["score"] == 0.0
    assert body["completeness"]["blocked"]


def test_put_then_get_roundtrips(client):
    r = client.put("/api/collections/c1/profile", json=PROFILE)
    assert r.status_code == 200, r.text
    assert r.json()["exists"] is True

    body = client.get("/api/collections/c1/profile").json()
    assert body["exists"] is True
    assert body["profile"]["display_name"] == "Henderson Household"
    assert body["profile"]["ips"]["max_single_position_pct"] == 8
    assert body["updated_at"]


def test_completeness_shrinks_as_the_profile_fills_in(client):
    before = client.get("/api/collections/c1/profile").json()["completeness"]
    client.put("/api/collections/c1/profile", json=PROFILE)
    after = client.get("/api/collections/c1/profile").json()["completeness"]

    assert after["score"] > before["score"]
    assert len(after["blocked"]) < len(before["blocked"])


def test_target_allocation_that_misses_100_is_warned_not_corrected(client):
    payload = {**PROFILE, "ips": {**PROFILE["ips"], "allocation_targets": [
        {"asset_class": "Equities", "target_pct": 60},
        {"asset_class": "Fixed Income", "target_pct": 27},
    ]}}
    body = client.put("/api/collections/c1/profile", json=payload).json()

    warning = body["completeness"]["target_allocation_warning"]
    assert "87" in warning and "100" in warning
    # The stored value is what was entered — not normalized behind their back.
    saved = client.get("/api/collections/c1/profile").json()
    assert [t["target_pct"] for t in saved["profile"]["ips"]["allocation_targets"]] == [60.0, 27.0]


def test_put_is_replace_not_merge(client):
    client.put("/api/collections/c1/profile", json=PROFILE)
    client.put("/api/collections/c1/profile", json={"display_name": "Renamed"})

    body = client.get("/api/collections/c1/profile").json()
    assert body["profile"]["display_name"] == "Renamed"
    assert body["profile"]["ips"]["allocation_targets"] == []


def test_invalid_payload_is_rejected(client):
    bad = {**PROFILE, "ips": {"allocation_targets": [{"asset_class": "Equities", "target_pct": 140}]}}
    r = client.put("/api/collections/c1/profile", json=bad)
    assert r.status_code == 422


def test_unknown_risk_tolerance_is_rejected(client):
    r = client.put("/api/collections/c1/profile", json={**PROFILE, "risk_tolerance": "yolo"})
    assert r.status_code == 422


def test_delete_removes_the_profile(client):
    client.put("/api/collections/c1/profile", json=PROFILE)
    r = client.delete("/api/collections/c1/profile")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert client.get("/api/collections/c1/profile").json()["exists"] is False


def test_profiles_do_not_leak_between_collections(client):
    client.put("/api/collections/c1/profile", json=PROFILE)
    body = client.get("/api/collections/c2/profile").json()
    assert body["exists"] is False
