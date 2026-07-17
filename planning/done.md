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
