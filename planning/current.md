# Current Sprint

## Sprint 5 — Purchasing and Receiving

### Supplier foundation

- [ ] Supplier domain rules
- [ ] Supplier entity
- [ ] Supplier repository
- [ ] Supplier service
- [ ] Supplier API
- [ ] Supplier SQLAdmin
- [ ] Alembic migration
- [ ] Supplier tests
- [ ] Authenticated attribution
- [ ] Docker smoke test

### Receipt design

- [ ] Receipt belongs to one Supplier
- [ ] Receipt may contain existing CatalogVariants
- [ ] Receipt may create a new Variant for an existing Product
- [ ] Receipt may create a new Product and Variant
- [ ] One Receipt may mix existing and newly created items
- [ ] ReceiptItem stores quantity and purchase price