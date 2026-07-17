# Current Sprint

## Sprint 7 — Pricing and Identifiers

### Goal

Prepare catalog variants for sale by adding explicit retail pricing and stable machine-readable identifiers.

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

### Sprint completion

- [ ] Authenticated audit attribution
- [ ] Docker smoke test
- [ ] Update project documentation
