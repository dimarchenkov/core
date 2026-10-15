# Sprint 4.19 — Rental Asset Identification & Seals

## Implemented

- `RentalAsset.serial_number` is optional. A rentable item with no serial number and no seals
  remains valid and can be issued normally.
- `RentalAssetSeal` is append-only operational history. It records a location, seal number,
  ACTIVE/REMOVED/BROKEN status, installation/removal timestamps and an optional note.
- Installing a new active seal at the same normalized location marks the previous active record
  REMOVED instead of replacing its number or deleting it.
- Physical issue freezes `asset_code`, the optional serial number and every currently ACTIVE seal
  into the immutable `RentalOrderItem.asset_identification_snapshot`.
- Agreement generation includes only identification that exists and freezes it in the agreement
  item snapshot and PDF bytes. It never prints empty dash placeholders.
- Return reads only the issue snapshot. Each expected seal can be recorded as OK, BROKEN, MISSING
  or MISMATCH; every non-OK result requires an operator note.
- Return history stores the expected location/number, verification result and note. Seal warnings
  do not create a new asset lifecycle or override the existing return-condition rules.

## Interface

- The RentalAsset passport has an optional serial-number editor and a “Контрольные пломбы”
  section with add, remove and broken actions. Historical seals stay visible.
- Return shows the expected serial and seals only when the issue snapshot contains them. “Все
  соответствуют” sets all expected seals to OK; per-seal problem choices expose a visible warning
  and require a comment before the server accepts the return.
- Completed return history displays the immutable verification results, including problem notes.

## Migration

`0047_rental_asset_identification` follows `0046_catalog_collections`, adds the optional serial
column, the `rental_asset_seals` history table, and immutable issue/return JSON snapshots on
`rental_order_items`.

## Physical UAT pending

Verify a no-identification issue, a serial-only item, an item with three real seals, replacement
history, agreement print, normal all-OK return and one problem return on desktop and iPhone. Check
that seal problems are visible but asset availability still follows the selected return condition.
