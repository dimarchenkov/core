# Current

## Current Epic

**Epic 9 — Catalog Management**

Управление реальными карточками товара через обычный UI Core без обращения к SQLAdmin.

## Current Sprint

**Sprint 9.11 — Catalog Management Foundation**

**Stage: User Acceptance / Real-world readiness**

### UAT pass: Intake Product Autosave / Catalog Media

- Product-поля черновика (category, name, description) сохраняются существующим PATCH:
  select — сразу, text — debounce 600 мс; запросы сериализованы, ошибки видны inline с retry.
- Product Save удалён только из Intake; Variant «Сохранить позицию» сохранён.
- Catalog Media управляется рядом с Product/Variant; отдельные Camera/Gallery inputs,
  primary/additional/unlink через существующие ImageLink API, явно обозначенный Product fallback.
- Domain model, reserved SKU/barcode и Media storage не менялись.
- Проверка реального iPhone/Safari Camera/Gallery и авторизованный UI smoke-test ещё нужны.
  Автоматические JS-проверки: `node --test tests/web_uat.test.cjs` (без npm/build pipeline).
- После autosave подсказки готовности Variant обновляются из ответа backend без
  перерисовки формы; добавлен regression test для устаревших подсказок категории/названия.
- Результаты прохода: полный pytest — 336 passed; JS — 7 passed; Ruff, JS syntax,
  `git diff --check` успешны; Alembic head — `0029_intake_label_identity`;
  Docker Compose работает, `/health` возвращает `{"status":"ok"}`.
- Browser smoke: `/app` открывается, но проверка карточек остановлена на авторизации.

Начиная с этого Sprint номер записывается как `Epic.Sprint`, при этом номер Sprint глобальный и
не сбрасывается между Epic. Исторические названия Sprint 1–10G сохраняются.

## Goal

По результатам реальной User Acceptance устранить концептуальный gap **Intake Workspace /
Ready-for-Sale Workflow**. Intake должен стать полным рабочим местом от создания или выбора
Product до маркировки, проведения и запуска публикации в AQSI без обязательного перехода в
Catalog. Sprint 9.11 остаётся открытым; Sprint 9.12 не начат.

## Scope

- одна позиция Intake включает Product с одним или несколькими Variant;
- Product содержит только общие данные и фото, а SKU, barcode, variant photo и коммерческие
  условия принадлежат Variant; quantity относится к Variant в Intake/Inventory;
- default Variant существует даже без видимых вариантов и может быть скрыт UI;
- draft Intake позволяет добавлять, удалять и редактировать Variant;
- label доступна сохранённому Variant со штрихкодом до Complete Intake и независимо от экрана
  создания;
- Complete Intake атомарно фиксирует Inventory и исторические закупочные факты;
- после Complete Intake доступна публикация всех нужных Variant в AQSI с per-variant status/retry;
- Catalog остаётся управлением существующим каталогом, а не обязательным продолжением Intake;
- barcode однозначно идентифицирует Variant; общий manufacturer barcode нескольких Variant не
  поддерживается;
- camera scanning требует HTTPS deployment; ручной ввод и аппаратный scanner остаются fallback.
- товарная этикетка 40 × 30 мм отделена от Ready-for-Sale; direct print использует настраиваемый
  CUPS adapter при host-mode запуске, а Docker сохраняет PDF/system-print fallback.

## Definition of Done

- оператор завершает обычную приёмку до состояния ready-for-sale без перехода в Catalog;
- одна Intake position корректно работает с одним и несколькими Variant;
- Product не получает quantity или цены; каждый товар имеет как минимум один Variant;
- варианты draft Intake можно добавлять, редактировать и удалять;
- label сохранённого Variant со штрихкодом доступна до проведения и из Catalog;
- проведение атомарно создаёт ожидаемые Inventory movements и purchase facts без дублей;
- AQSI запускается для Intake после проведения, показывает результат каждого Variant и позволяет
  повторить ошибки;
- один barcode не принадлежит нескольким Variant, ambiguous lookup отсутствует;
- camera scan проверен по HTTPS на целевых телефонах, fallback понятен;
- существующие barcode, legacy labels и RENT identity не меняют семантику;
- Ruff, тесты, миграционная цепочка, Docker smoke test и `/health` проходят;
- результаты ручного real-world smoke-test честно зафиксированы.
