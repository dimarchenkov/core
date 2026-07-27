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

## P1 — Ready for Sale

- [x] Retail pricing and product identifiers
- [x] Derived ready-for-sale checks
- [x] Printer-independent 58 x 40 mm label and standard PDF printing
- [x] AQSI product publication boundary and field mapping
- [x] AQSI asynchronous product publication implementation
- [x] Controlled live AQSI smoke test with one dedicated Variant
- [ ] Runtime versioned AQSI fiscal profile and controlled bulk republishing
- [x] End-to-end photo-first intake workflow from phone

## Deferred — Local Printing Infrastructure

- [ ] Physically calibrate XPrinter XP-365B with 58 x 40 mm labels
- [ ] Verify thermal barcode scanning, margins, darkness and gap detection
- [ ] Add a local TSPL-over-USB print adapter when the local server is available
- [ ] Preserve standard PDF printing as the universal fallback

## P2 — Workflow UX

- [x] Photo-first mobile Receive Goods workflow
- [x] Derived Ready for Sale employee attention API
- [ ] Ready for Sale employee queue interface
- [x] Employee activity feed API
- [x] First-party activity feed interface
- [ ] Employee workflow timing, errors and cancellation metrics
- [ ] Reference/history cards separated from daily workflow screens

## P3 — Rental Foundation

- [ ] Rental Asset and Asset Code
- [ ] Checkout and return lifecycle foundation
- [ ] Before/after condition photos
- [ ] Seals, maintenance and damage history
- [ ] Rental pricing and deposits

### Epic — Rental Operations and Economics

#### Rental operations

- [ ] Issue a `RentalAsset` to a customer.
- [ ] Accept a returned `RentalAsset` and record its resulting condition.
- [ ] Record the actual rental amount attributed to each `RentalAsset`.
- [ ] Preserve the append-only history of completed rentals.
- [ ] Send an available asset to maintenance and return it to operation.
- [ ] Record before/after condition photos, damage and seal changes.

#### Rental analytics

- [ ] Define the source of `acquisition_cost` for each `RentalAsset`.
- [ ] Define how `preparation_cost` is recorded.
- [ ] Derive `invested_cost` from acquisition and preparation costs.
- [ ] Calculate `completed_rentals_count` from completed rental history.
- [ ] Calculate `lifetime_revenue` from actual attributed rental income.
- [ ] Calculate `payback_percent` and `is_paid_back`.
- [ ] Identify the `first_profitable_rental` after payback.
- [ ] Show the statuses «Окупается», «Окупился» and «Окупился + одна аренда».
- [ ] Define allocation rules for discounts, delivery and shared order amounts before financial analytics.
- [ ] Aggregate Rental economics by `Variant` and `Product`.

## P4 — Sell

- [ ] Sales document and lifecycle
- [ ] Sale inventory movements
- [ ] Returns and corrections
- [ ] AQSI sales synchronization

## P5 — Warehouse Operations

- [ ] Stock balance read API
- [ ] Media processing: master/WebP generation
- [x] Authenticated source-image delivery
- [ ] Media delivery: generated master download
- [ ] Brand support
- [ ] CSV import from Tilda
- [ ] Inventory counting and adjustments
- [ ] Write-offs and transfers

## P6 — Marketplace and Messaging

- [ ] Tilda Sync
- [ ] Telegram
- [ ] MAX
- [ ] Wildberries
- [ ] Яндекс Маркет

## P7 — Productization

- [ ] Zero-to-Working bootstrap
- [ ] Deployment and upgrade guide
- [ ] User and administrator guides
- [ ] Versioned developer API guide
- [ ] Subscription deployment model
- [ ] Franchise operations guide
- [ ] Investor and partner materials
- [x] Optional first-party intake frontend
- [ ] Employee activity feed and operational analytics
- [ ] Multi Warehouse

## Technical debt

### Catalog

- Discuss renaming `CatalogVariant.title` to `variant_name` or `display_name`.
- Design `AssetCodeGenerator` for future rental assets.

### Infrastructure

- Configure Angie to re-resolve the Docker `api` service after container recreation.
- Add an API healthcheck and make Angie depend on API health.
- Add recovery for an AQSI publication attempt left in `processing` after a hard worker crash.
