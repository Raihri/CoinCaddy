"""Liveness and readiness checks for the API, database schema, and mock-service boundary."""

import httpx
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config import get_settings
from app.contracts.business_time import utc_now
from app.db import get_engine

router = APIRouter(prefix="/api/health", tags=["health"])

# The Alembic revision this build of the API expects. Bump together with new migrations.
EXPECTED_SCHEMA_REVISION = "0008_classification"


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok", "checked_at": utc_now().isoformat()}


def _check_database() -> dict[str, str]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 - report, never leak connection strings
        return {"status": "error", "detail": type(exc).__name__}
    if revision != EXPECTED_SCHEMA_REVISION:
        return {"status": "error", "detail": f"schema revision {revision!r}, expected {EXPECTED_SCHEMA_REVISION!r}"}
    return {"status": "ok", "schema_revision": revision}


def _check_mock_services() -> dict[str, str]:
    try:
        resp = httpx.get(f"{get_settings().mock_services_url}/health", timeout=2.0)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__}
    return {"status": "ok" if body.get("status") == "ok" else "error", "mode": str(body.get("mode"))}


@router.get("/ready")
def ready(response: Response) -> dict[str, object]:
    checks = {"database": _check_database(), "mock_services": _check_mock_services()}
    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": overall, "checks": checks, "checked_at": utc_now().isoformat()}
