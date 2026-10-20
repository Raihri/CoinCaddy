"""Health checks against the real development database (run migrations first)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.health import router as health_router
from mock_services.app import app as mock_app

client = TestClient(app)


@pytest.fixture
def mock_services_up(monkeypatch):
    mock_client = TestClient(mock_app)
    monkeypatch.setattr(health_router.httpx, "get", lambda url, timeout: mock_client.get("/health"))


@pytest.fixture
def mock_services_down(monkeypatch):
    def refuse(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(health_router.httpx, "get", refuse)


def test_live():
    r = client.get("/api/health/live")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_ready_when_database_and_mock_services_are_up(mock_services_up):
    r = client.get("/api/health/ready")
    body = r.json()
    assert r.status_code == 200, body
    assert body["checks"]["database"] == {"status": "ok", "schema_revision": "0012_funding_matches"}
    assert body["checks"]["mock_services"] == {"status": "ok", "mode": "sandbox"}


def test_ready_reports_degraded_without_mock_services(mock_services_down):
    r = client.get("/api/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["mock_services"]["status"] == "error"
