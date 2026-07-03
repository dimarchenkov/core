from __future__ import annotations

import os

from fastapi.testclient import TestClient


def _build_client() -> TestClient:
    os.environ.setdefault("CORE_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    from core.main import create_app

    return TestClient(create_app())


def test_health_endpoint_returns_ok() -> None:
    client = _build_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_swagger_ui_is_available() -> None:
    client = _build_client()

    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text
