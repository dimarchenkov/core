# Current

## Current Epic

**Epic 9 — Catalog Management**

Управление реальными карточками товара через обычный UI Core без обращения к SQLAdmin.

## Current Sprint

**Sprint 9.11 — Catalog Management Foundation**

**Stage: User Acceptance / Real-world readiness**

Начиная с этого Sprint номер записывается как `Epic.Sprint`, при этом номер Sprint глобальный и
не сбрасывается между Epic. Исторические названия Sprint 1–10G сохраняются.

## Goal

Устранить последние эксплуатационные blockers Sprint 9.11 перед реальной приёмкой товара. В
текущем проходе Variant получает несколько штрихкодов, а Intake становится barcode-first без
изменения границ Catalog, Inventory, Rental и AQSI.

## Scope

- несколько глобально уникальных штрихкодов Variant с источниками `INTERNAL` и `MANUFACTURER`;
- миграция существующих внутренних EAN без изменения их значений;
- единый barcode lookup для EAN-13, EAN-8, UPC-A и Code 128;
- Intake: ручной ввод, аппаратный сканер с Enter и камера используют один lookup;
- неизвестный код переносится в новый Product/Variant как manufacturer barcode;
- Catalog показывает и позволяет добавить manufacturer barcode;
- AQSI предпочитает подходящий manufacturer barcode и откатывается к внутреннему EAN;
- товарные этикетки сохраняют прежний внутренний EAN, RENT остаётся отдельной идентичностью.

## Definition of Done

- существующие Variant сохраняют прежний internal EAN и получают запись `INTERNAL`;
- один Variant принимает несколько кодов, но один код не может принадлежать двум Variant;
- Intake находит существующий Variant по любому коду и не требует повторного ввода неизвестного;
- камера имеет понятный fallback на ручной ввод при отсутствии API, разрешения или распознавания;
- AQSI получает manufacturer barcode только когда он соответствует текущему контракту;
- legacy labels и RENT identity не меняют семантику;
- Ruff, тесты, миграционная цепочка, Docker smoke test и `/health` проходят;
- ручной UI smoke-test и ограничения физического сканирования честно зафиксированы в отчёте.
