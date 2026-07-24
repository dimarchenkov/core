# Architecture Review v2 — Product Readiness after Sprint 8

## Assessment rule

Statuses are based on executable code, registered routes, models and tests at `v0.5.0`, not on
roadmap wording. “Partially implemented” means a usable subset exists but a documented product
surface or lifecycle is absent.

| Capability | Status | Evidence and limits |
| --- | --- | --- |
| Product creation | Implemented | Product CRUD is registered under Catalog (`src/core/catalog/routes.py:195-304`); Complete Intake creates a Product through `CatalogProductService` (`src/core/intake/completion.py:203-213`). |
| Variant creation | Implemented | Variant CRUD and stable identifier generation (`src/core/catalog/routes.py:308-426`, `src/core/catalog/service.py:247-321`). Intake creates or reuses a Variant (`src/core/intake/completion.py:197-235`). |
| Photos | Partially implemented | Authenticated upload, metadata, source delivery and ImageLink API exist (`src/core/media/routes.py:65-321`). Source validation and local atomic storage exist. `master_key`, `web_key` and `thumb_key` remain nullable and no processing pipeline produces them (`src/core/media/models.py:25-28`). |
| Supplier | Implemented | Supplier CRUD API and service (`src/core/supplier/routes.py:33-105`, `src/core/supplier/service.py:22-101`). |
| Receipt | Implemented | Draft Receipt and item API (`src/core/receipt/routes.py:67-338`) with Supplier and Variant references (`src/core/receipt/models.py:18-64`). |
| Receipt posting | Implemented | `POST /api/receipts/{id}/post`; workflow validates/locks and emits movements (`src/core/receipt/routes.py:98-126`, `src/core/receipt/posting.py:25-101`). |
| Receipt cancellation | Implemented | `POST /api/receipts/{id}/cancel`; reversal movements preserve the ledger (`src/core/receipt/routes.py:129-150`, `src/core/receipt/cancellation.py:19-63`). |
| Inventory | Partially implemented | Immutable ledger, balances and history services exist (`src/core/inventory/service.py:42-138`). There is no direct Inventory router or stock-balance API in `src/core/main.py:36-50`. |
| Pricing | Implemented for current sale scope | Set/current/history API and immutable RUB price facts (`src/core/pricing/routes.py:35-104`, `src/core/pricing/models.py:18-39`). Rental price/deposit behavior is absent. |
| Barcode | Implemented | Variant owns generated stable barcode; exact scanner lookup endpoint exists (`src/core/catalog/models.py:79-80`, `src/core/catalog/routes.py:345-357`). |
| Labels | Implemented | Protected 58×40 PDF endpoint and renderer (`src/core/labels/routes.py:31-49`, `src/core/labels/renderer.py`). Physical printer calibration/adapter is intentionally outside this implementation. |
| Ready for Sale | Implemented in API; UI partial | Derived single-Variant check and attention API (`src/core/readiness/routes.py:37-65`); shared policy uses image, SKU, barcode and positive retail RUB price (`src/core/readiness/policy.py:22-43`). `/app` has completion reasons, but the dedicated employee queue interface remains backlog. |
| AQSI | Implemented with operational limitation | Authenticated enqueue/status/history API, payload builder, RQ job and checkpoint processor (`src/core/integrations/aqsi/routes.py:53-133`, `src/core/integrations/aqsi/processor.py:23-269`). Disabled by default (`.env.example:9-11`); hard-crash recovery for attempts left in `processing` remains backlog. |
| Authentication | Implemented | Login and current-user endpoints (`src/core/identity/routes.py:20-52`), Argon2 password support/JWT (`src/core/identity/security.py`) and protected business dependencies (`src/core/identity/dependencies.py:17-37`). |
| Mobile Intake | Implemented | First-party `/app` route (`src/core/web/routes.py:12-15`), static phone-first client, resumable IntakeSession commands and atomic completion (`src/core/intake/routes.py:138-326`). |
| Search | Partially implemented | Exact barcode lookup (`src/core/catalog/routes.py:345-357`) and Ready for Sale search across barcode/SKU/Product/Variant title (`src/core/readiness/read_service.py:96-107`). No general Catalog search endpoint or full employee search surface was found. |
| Import | Absent | No import package, route, service or job is registered in `src/core/main.py:36-50`; CSV import remains backlog. |
| Export | Absent | No export route/service/job was found. |
| Rental | Absent | `src/core/rental/__init__.py` is only a reserved package; no Asset, contract, checkout, return or condition model/service/route/migration exists. |

## Sprint 8 workflow readiness

The Receive Goods workflow is operationally complete at the code boundary:

1. an authenticated employee creates/resumes an `IntakeSession`
   (`src/core/intake/routes.py:138-166`);
2. existing Variants are identified by ID/barcode without a new photo, while new positions
   require a source photo (`src/core/intake/draft_service.py:115-202`);
3. quantity, purchase price and Supplier are progressively captured
   (`src/core/intake/draft_service.py:98-113,204-226`);
4. completion locks the session and atomically creates Catalog facts, Receipt, items,
   StockMovements and ActivityEvent (`src/core/intake/completion.py:69-150`);
5. the response derives Ready for Sale reasons from current authoritative facts
   (`src/core/intake/completion.py:137-145`).

The deprecated one-shot endpoint remains executable (`src/core/intake/routes.py:96-134`). This
does not block the new workflow but leaves two Intake entry paths until the documented removal
gate is met.

## Product claims versus current interface

README accurately describes `/app`, AQSI configuration and administrative CLI
(`README.md:34-132`). Its “Основные возможности” list includes Tilda and Rental
(`README.md:17-25`), while neither has an executable model, route or workflow in this release.
The README status does say the project is in active development (`README.md:31`), but the feature
list does not distinguish implemented capabilities from target scope.

