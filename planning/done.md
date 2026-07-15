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
- ✅ Local image upload
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

## Sprint 5 — Supplier Management

### Supplier foundation

- [x] Supplier domain rules
- [x] Supplier entity
- [x] Supplier repository
- [x] Supplier service
- [x] Supplier API
- [x] Supplier SQLAdmin
- [x] Alembic migration
- [x] Supplier tests
- [x] Authenticated attribution
- [x] Docker smoke test

### Receipt design

- [x] Receipt belongs to one Supplier
- [x] ReceiptItem references CatalogVariant
- [x] New catalog items are registered through Intake
- [x] One Receipt may mix existing and newly registered Variants
- [x] ReceiptItem stores quantity and purchase price
- [x] Draft Receipt does not affect stock

### Receipt draft foundation

- [x] Receipt entity
- [x] ReceiptItem entity
- [x] Receipt number generation
- [x] Draft lifecycle rules
- [x] Receipt repository
- [x] Receipt service
- [x] Receipt API
- [x] Receipt SQLAdmin
- [x] Alembic migration
- [x] Tests
- [x] Authenticated attribution
- [ ] Docker smoke test