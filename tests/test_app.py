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
    assert "/events" in script.text
    assert "text/event-stream" in script.text
    assert "expected_intake_version" in script.text
    assert "Синхронизировано" in script.text
    assert "25000" in script.text
    assert "missing_retail_price" in script.text
    assert 'name="rental_quantity"' in script.text
    assert "Из них в аренду" in script.text
    assert "Возврат аренды" in script.text
    assert "/return-preview" in script.text
    assert "`/rental/orders/${state.rental.order.id}/return`" in script.text
    assert "Просрочен" in script.text
    assert "/api/operations/catalog/products" in script.text
    assert "/api/operations/rental/assets" in script.text
    assert "📷 Сканировать камерой" in script.text
    assert "startBarcodeCamera" in script.text
    assert "acceptScannedBarcode" in script.text
    assert 'await runCatalogSearch("variant", input.value, true)' in script.text
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
    assert "/api/rental/variants/${variant.id}/tariffs?view=management" in script.text
    assert "openRentalTariffEditor" in script.text
    assert "Добавить тариф" in script.text
    assert "По длительности" in script.text
    assert "Временной слот" in script.text
    assert "Договор обязателен" in script.text
    assert "История" in script.text
    assert "/tariff-items" in script.text
    assert "Другой срок" in script.text
    assert "Срок начнётся в момент фактической выдачи" in script.text
    assert "Итоговые условия заказа" in script.text
    assert "Позиция сохранена" in script.text
    assert "Добавить оборудование" in script.text
    assert "Аренда подготовлена и готова к выдаче" in script.text
    assert 'id="rental-ready-issue"' in script.text
    assert "Ожидают выдачи" in script.text
    assert "Выдать оборудование" in script.text
    assert "Сформировать договор" in script.text
    assert "Договор проката" in script.text
    assert "Паспортные данные в Core не сохраняются" in script.text
    assert "/agreement-preview" in script.text
    assert "/api/settings/rental/agreement" in script.text
    assert "Принять возврат" in script.text
    assert "data-rental-due" in script.text
    assert "Просрочка" in script.text
    assert "Оформить возврат" in script.text
    assert "Предварительный расчёт" in script.text
    assert "/issue-preview" in script.text
    assert 'name="slot_start" type="time"' in script.text
    assert 'name="valid_from" type="date"' in script.text
    assert ".tariff-list" in styles.text
    assert ".tariff-time-row" in styles.text
    assert ".tariff-choice-list" in styles.text
    assert ".rental-ready-summary" in styles.text
    assert ".rental-operation-card" in styles.text
    assert ".issue-preview-list" in styles.text
    assert ".return-calculation" in styles.text
    assert ".agreement-preview-shell" in styles.text
    assert ".agreement-settings-card" in styles.text
    assert "grid-template-columns: minmax(0, 1fr)" in styles.text
    assert "Цена не указана" in script.text
    assert "/api/labels/variants/" in script.text
    assert "/api/labels/variants/print-capability" in script.text
    assert 'data-print-label="${variant.id}"' in script.text
    assert "Системная печать" in script.text
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
    schema = response.json()
    assert schema["info"]["title"] == "Core"
    assert "/api/rental/variants/{variant_id}/tariffs" in schema["paths"]
    assert "/api/rental/tariffs/{tariff_id}" in schema["paths"]
    assert "/api/rental/tariffs/{tariff_id}/history" in schema["paths"]
    assert "/api/rental/tariffs/{tariff_id}/archive" in schema["paths"]
    assert "/rental/orders/{order_id}/tariff-items" in schema["paths"]
    assert "/rental/orders/{order_id}/tariff-items/{item_id}" in schema["paths"]
    assert "/rental/orders/{order_id}/issue-preview" in schema["paths"]
    assert "/rental/orders/{order_id}/issue" in schema["paths"]
    assert "/api/settings/rental/agreement" in schema["paths"]
    assert "/api/settings/rental/agreement/lessor" in schema["paths"]
    assert "/api/settings/rental/agreement/templates" in schema["paths"]
    assert "/api/settings/rental/agreement/preview" in schema["paths"]
    assert "/rental/orders/{order_id}/agreement" in schema["paths"]
    assert "/rental/orders/{order_id}/agreement-preview" in schema["paths"]
    assert "/rental/orders/{order_id}/agreements" in schema["paths"]
    assert "/rental/agreements/{agreement_id}/pdf" in schema["paths"]


def test_legacy_intake_openapi_operation_is_deprecated() -> None:
    """Swagger directs new clients to the resumable IntakeSession workflow."""
    client = _build_client()

    operation = client.get("/openapi.json").json()["paths"]["/api/intake"]["post"]

    assert operation["deprecated"] is True
    assert "/api/intake/sessions" in operation["description"]
