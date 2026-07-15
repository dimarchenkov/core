# Current Sprint

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
- [ ] Docker smoke test

### Receipt design

- [ ] Receipt belongs to one Supplier
- [ ] Receipt may contain existing CatalogVariants
- [ ] Receipt may create a new Variant for an existing Product
- [ ] Receipt may create a new Product and Variant
- [ ] One Receipt may mix existing and newly created items
- [ ] ReceiptItem stores quantity and purchase price
