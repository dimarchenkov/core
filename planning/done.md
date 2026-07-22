# Done

## Phase 0 — Planning

- ✅ Vision
- ✅ Modules
- ✅ Business Processes
- ✅ Architecture
- ✅ MVP
- ✅ AI Rules
- ✅ README

## Sprint 1 — Infrastructure

- ✅ Docker
- ✅ FastAPI
- ✅ SQLAdmin
- ✅ Angie
- ✅ Redis

## Sprint 2 — Shared Foundation

- ✅ UUIDv7 mixin
- ✅ Timestamp mixin
- ✅ SoftDelete mixin
- ✅ Version mixin
- ✅ User tracking mixin
- ✅ BaseModel
- ✅ Money foundation
- ✅ Category entity

## Sprint 3 — Catalog and Intake

- ✅ CatalogProduct
- ✅ CatalogVariant
- ✅ SKU generation
- ✅ Image
- ✅ ImageLink
- ✅ Image roles and primary image rules
- ✅ Local image upload and original storage
- ✅ Intake workflow
- ✅ Category API
- ✅ Docker foundation checkpoint

## Sprint 4 — Identity Lite

- ✅ User entity
- ✅ Argon2id password hashing
- ✅ Administrator creation CLI
- ✅ Emergency superuser CLI
- ✅ Privilege audit events
- ✅ Login endpoint
- ✅ JWT access token
- ✅ Current user dependency
- ✅ Protected business API
- ✅ created_by attribution
- ✅ updated_by attribution
- ✅ deleted_by attribution
- ✅ Docker authentication smoke test

## Sprint 5 — Supplier Management and Receipt Drafts

### Supplier foundation

- ✅ Supplier domain rules
- ✅ Supplier entity
- ✅ Supplier repository
- ✅ Supplier service
- ✅ Supplier API
- ✅ Supplier SQLAdmin
- ✅ Alembic migration
- ✅ Supplier tests
- ✅ Authenticated attribution
- ✅ Docker smoke test

### Receipt design

- ✅ Receipt belongs to one Supplier
- ✅ ReceiptItem references CatalogVariant
- ✅ New catalog items are registered through Intake
- ✅ One Receipt may mix existing and newly registered Variants
- ✅ ReceiptItem stores quantity and purchase price
- ✅ Draft Receipt does not affect stock

### Receipt draft foundation

- ✅ Receipt entity
- ✅ ReceiptItem entity
- ✅ Receipt number generation
- ✅ Draft lifecycle rules
- ✅ Receipt repository
- ✅ Receipt service
- ✅ Receipt API
- ✅ Receipt SQLAdmin
- ✅ Alembic migration
- ✅ Tests
- ✅ Authenticated attribution
- ✅ Docker smoke test

## Sprint 6 — Inventory Engine

### Inventory design

- ✅ Immutable stock movement ledger
- ✅ Movement types
- ✅ Balance calculation strategy
- ✅ Receipt posting rules
- ✅ Receipt cancellation rules
- ✅ Reversal movements instead of deletion

### Inventory foundation

- ✅ StockMovement entity
- ✅ Movement repository
- ✅ Inventory service
- ✅ Receipt posting service
- ✅ Receipt cancellation service
- ✅ Row locking for posting and cancellation
- ✅ Alembic migrations
- ✅ Unit and integration tests
- ✅ Docker end-to-end smoke test
- ✅ Verified receipt lifecycle: draft → posted → cancelled
- ✅ Verified ledger balance returns to zero after reversal

## Sprint 7 — Ready for Sale

### Pricing and identifiers

- ✅ Immutable retail price history with actor attribution
- ✅ Current retail price API and SQLAdmin view
- ✅ Automatic internal EAN-13-compatible barcode generation
- ✅ Exact scanner lookup by primary barcode
- ✅ Barcode migration for existing Variants

### Sale readiness and labels

- ✅ Derived Ready for Sale checks
- ✅ Required active primary image, SKU, barcode and positive retail RUB price
- ✅ Authenticated readiness API with explicit missing requirements
- ✅ Printer-independent 58 × 40 mm PDF label
- ✅ Standard driver-managed printing boundary

### AQSI publication

- ✅ Minimal ordinary-goods payload with VAT-exempt code `6`
- ✅ Stable AQSI identity based on CatalogVariant UUID
- ✅ Deterministic `Товары Core` fallback category
- ✅ Asynchronous, idempotent and actor-attributed publication attempts
- ✅ AQSI shop discovery and retail-price binding
- ✅ Remote verification before `published`
- ✅ Worker scheduler for delayed retries
- ✅ Authenticated command, status and history API
- ✅ Alembic migrations through `0014_create_publications`

### Verification

- ✅ 214 automated tests
- ✅ Docker rebuild and migration smoke test
- ✅ Real AQSI publication of one dedicated Variant
- ✅ Cash-register lookup by generated barcode
- ✅ Repeated publication command verified idempotent

## Sprint 8 — Workflow UX

### Architecture and workflow boundaries

- ✅ Architecture Backlog v1 with module, service, transaction and domain maps
- ✅ Explicit transaction ownership across Catalog, Media, Receipt and Intake commands
- ✅ Workflow layer documented without a generic BPM engine
- ✅ Legacy one-shot Intake API deprecated in favor of resumable sessions
- ✅ Ready for Sale remains derived rather than persisted workflow state

### Resumable mobile intake

- ✅ Employee-owned `IntakeSession` and `IntakeItemDraft`
- ✅ Identification-first flow without mandatory early Supplier selection
- ✅ Photo First for a new Product or Variant
- ✅ Barcode, SKU and Variant selection for repeat delivery without a mandatory new photo
- ✅ Progressive quantity, purchase-price and Supplier capture
- ✅ Resume and explicit abandonment rules
- ✅ Derived missing requirements for every item and session

### Atomic completion and operational visibility

- ✅ Atomic Product, Variant, primary image, Receipt and inventory completion
- ✅ Idempotent retry and rollback behavior
- ✅ Employee activity events and personal activity feed API
- ✅ Derived Ready for Sale attention queue API
- ✅ Authenticated source-image delivery

### First-party workflow interface

- ✅ Mobile `/app` entry point with local authentication
- ✅ Start and resume screen
- ✅ Scanner/search and photograph-new-item actions
- ✅ Existing primary photo confirmation
- ✅ Guided forms and late Supplier selection
- ✅ Receipt completion with explicit Ready for Sale follow-up reasons
- ✅ Draft fields preserved across workflow transitions

### Verification

- ✅ 218 automated tests
- ✅ Docker rebuild and migrations through `0016_create_activity_events`
- ✅ Controlled phone smoke test through Angie
- ✅ Real `REC-000007` posting with one immutable `+200` inventory movement
- ✅ API logs, completed IntakeSession and database state verified
