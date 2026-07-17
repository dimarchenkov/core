# Backlog

## Completed foundations

- [x] Infrastructure
- [x] Shared database foundation
- [x] Catalog foundation
- [x] Identity Lite
- [x] Supplier management
- [x] Receipt drafts
- [x] Inventory ledger, posting and cancellation
- [x] Media foundation: image metadata, links, primary image and local original upload

## P1 — MVP remaining

- [ ] Pricing and product identifiers
- [ ] Stock balance read API
- [ ] Media processing: master/WebP generation
- [ ] Media delivery: source and master download
- [ ] Brand support
- [ ] Label generation and printing
- [ ] CSV import from Tilda
- [ ] AQSI integration
- [ ] End-to-end intake workflow from phone

## P2 — After MVP

- [ ] Rental
- [ ] Tilda Sync
- [ ] Telegram
- [ ] MAX

## P3 — Future

- [ ] React UI
- [ ] Wildberries
- [ ] Яндекс Маркет
- [ ] Analytics
- [ ] Multi Warehouse

## Technical debt

### Catalog

- Discuss renaming `CatalogVariant.title` to `variant_name` or `display_name`.
- Design `AssetCodeGenerator` for future rental assets.

### Infrastructure

- Configure Angie to re-resolve the Docker `api` service after container recreation.
- Add an API healthcheck and make Angie depend on API health.
