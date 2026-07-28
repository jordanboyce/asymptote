"""Tests for the AUTH_PASSWORD shared-secret middleware (public-deploy gate).

The middleware is registered at import time only when AUTH_PASSWORD is set,
so these tests reload config + main with the variable present and restore
the unauthenticated app afterwards.
"""

import base64
import importlib

import pytest
from fastapi.testclient import TestClient

SECRET = "test-deploy-secret"


@pytest.fixture()
def auth_client(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", SECRET)
    import config
    import main

    importlib.reload(config)
    main = importlib.reload(main)
    # Bare client (no context manager): lifespan is skipped, middleware still runs.
    yield TestClient(main.app)

    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    importlib.reload(config)
    importlib.reload(main)


def test_health_stays_open(auth_client):
    """Platform health probes (Docker, Railway) must never require auth."""
    response = auth_client.get("/health")
    assert response.status_code == 200


def test_unauthenticated_request_rejected(auth_client):
    response = auth_client.get("/documents/upload/active")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


def test_wrong_password_rejected(auth_client):
    response = auth_client.get(
        "/documents/upload/active", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401


def test_bearer_password_accepted(auth_client):
    response = auth_client.get(
        "/documents/upload/active", headers={"Authorization": f"Bearer {SECRET}"}
    )
    assert response.status_code != 401


def test_basic_password_accepted_any_username(auth_client):
    token = base64.b64encode(f"anyone:{SECRET}".encode()).decode()
    response = auth_client.get(
        "/documents/upload/active", headers={"Authorization": f"Basic {token}"}
    )
    assert response.status_code != 401
