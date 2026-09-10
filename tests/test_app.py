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
    assert "Возврат аренды" in script.text
    assert "/complete-items" in script.text
    assert "Просрочен" in script.text
    assert "/api/operations/catalog/products" in script.text
    assert "/api/operations/rental/assets" in script.text
    assert "📷 Сканировать камерой" in script.text
    assert "startBarcodeCamera" in script.text
    assert "acceptScannedBarcode" in script.text
    assert "await lookupIntakeBarcode(input.value)" in script.text
    assert "await identifyDraftManufacturerBarcode(input)" in script.text
    assert "@zxing/browser@0.2.1" in script.text
    assert 'facingMode: { ideal: "environment" }' in script.text
    assert "getUserMedia" in script.text
    assert "/passport" in script.text
    assert "/maintenance" in script.text
    assert "/damages" in script.text
    assert "/condition-photos" in script.text
    assert "/history" in script.text
    assert "Каталог" in script.text
    assert "Экземпляры" not in script.text
    assert "Цена продажи" in script.text
    assert "Нужно указать цену" in script.text
    assert "/api/labels/variants/" in script.text
    assert "/api/labels/variants/print-capability" in script.text
    assert 'data-print-label="${variant.id}">Системная печать' in script.text
    assert "printVariantLabels(button.dataset.printLabel, 1)" in script.text
    assert "openVariantLabel(button.dataset.printLabel, true)" not in script.text
    assert "data-direct-print-label" not in script.text
    assert "data-print-draft-system" not in script.text
    assert "groupIntakeItems" in script.text
    assert "renderProductGroup" in script.text
    assert 'pluralizeRu(productCount, "товар", "товара", "товаров")' in script.text
    assert "Варианты ·" in script.text
    assert "Удалить товар из приёмки" in script.text
    assert "Удалить черновик приёмки" in script.text
    assert 'state.user?.is_admin' in script.text
    assert 'method: "DELETE"' in script.text
    assert "Отменить действие будет нельзя" in script.text
    assert 'data-image-preview="${image.image_id}"' in script.text
    assert 'data-image-preview="${link.image_id}"' in script.text
    assert "Используется общее фото товара" in script.text
    assert "openImagePreview" in script.text
    assert "/api/media/images/${id}/source" in script.text
    assert 'event.key !== "Enter" && event.key !== " "' in script.text
    assert ".image-preview-dialog" in styles.text
    assert "object-fit: contain" in styles.text
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
