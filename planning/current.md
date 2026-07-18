# Current Sprint

## Sprint 7 — Ready for Sale

### Goal

Turn a received CatalogVariant into a sellable item with a clear readiness state, retail price, barcode, label and AQSI publication path.

### Product principles

- [x] Photo First is mandatory for new physical objects
- [x] API First: every business capability remains interface-independent
- [x] Rental remains a first-class lifecycle alongside sales
- [x] Employee attribution and future operational metrics remain required
- [x] Deployment, bootstrap and documentation are product concerns

### Pricing design

- [ ] Define the pricing domain rules
- [ ] Decide whether prices are current-state fields or an append-only history
- [ ] Define money precision and currency rules
- [ ] Define where purchase price belongs after receipt posting
- [ ] Define retail price update rules

### Pricing foundation

- [ ] Price entity or catalog pricing fields
- [ ] Pricing repository
- [ ] Pricing service
- [ ] Pricing API
- [ ] Pricing SQLAdmin integration
- [ ] Alembic migration
- [ ] Tests

### Product identifiers

- [ ] Define barcode format and ownership rules
- [ ] Implement automatic barcode generation
- [ ] Guarantee barcode uniqueness
- [ ] Expose barcode through catalog API and SQLAdmin
- [ ] Add barcode tests

### Ready-for-sale workflow

- [ ] Define readiness as derived checks, not one overloaded universal status
- [ ] Require an active primary image
- [ ] Require an active Variant with SKU
- [ ] Require a retail price
- [ ] Require a barcode
- [ ] Add a read API for missing readiness requirements
- [ ] Generate and print a basic label
- [ ] Define the AQSI publication boundary

### Sprint completion

- [ ] Authenticated audit attribution
- [ ] Docker smoke test
- [ ] Update project documentation
