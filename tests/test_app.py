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


def test_phone_first_workflow_interface_is_available() -> None:
    """Core delivers its first-party intake client without a separate frontend service."""
    client = _build_client()

    page = client.get("/app")
    script = client.get("/app/assets/app.js")
    styles = client.get("/app/assets/styles.css")

    assert page.status_code == 200
    assert "Core — Приёмка" in page.text
    assert script.status_code == 200
    assert "/api/intake/sessions" in script.text
    assert "saveAllItemForms" in script.text
    assert "missing_retail_price" in script.text
    assert 'name="rental_quantity"' in script.text
    assert "Из них в аренду" in script.text
    assert styles.status_code == 200
    assert "viewport-fit=cover" in page.text


def test_openapi_schema_is_public() -> None:
    """OpenAPI remains available without authentication for Swagger clients."""
    client = _build_client()

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Core"


def test_legacy_intake_openapi_operation_is_deprecated() -> None:
    """Swagger directs new clients to the resumable IntakeSession workflow."""
    client = _build_client()

    operation = client.get("/openapi.json").json()["paths"]["/api/intake"]["post"]

    assert operation["deprecated"] is True
    assert "/api/intake/sessions" in operation["description"]
