# Sprint 4.19 — Rental Asset Identification, Seals & Components

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
- `RentalAssetComponent` is an optional ordered checklist owned by one physical RentalAsset. It is
  not Product, Variant, SKU, stock balance or an independently rentable item.
- Add/edit/reorder changes only future issues. Removing a component deactivates its row; old issue,
  agreement and return snapshots retain the original name, note and order.
- Issue freezes active components into `asset_identification_snapshot`. Return accepts PRESENT,
  MISSING or DAMAGED for every snapshotted component and stores the immutable result separately.
- MISSING reuses the existing `INCOMPLETE` return condition; DAMAGED reuses `DAMAGED` when the
  operator has not already chosen a stronger existing outcome. Problem results require a note and
  do not calculate a charge automatically.

## Interface

- The RentalAsset passport has an optional serial-number editor and a “Контрольные пломбы”
  section with add, remove and broken actions. Historical seals stay visible.
- Return shows the expected serial and seals only when the issue snapshot contains them. “Все
  соответствуют” sets all expected seals to OK; per-seal problem choices expose a visible warning
  and require a comment before the server accepts the return.
- Completed return history displays the immutable verification results, including problem notes.
- The RentalAsset passport adds a compact “Комплектность” editor with add, edit, deactivate and
  up/down ordering. An empty asset shows only the unobtrusive add action.
- Return presents serial, seals and components in one identification block. “Все элементы на
  месте” confirms PRESENT in one action, with per-component MISSING/DAMAGED controls when needed.

## Migration

`0047_rental_asset_identification` follows `0046_catalog_collections`, adds the optional serial
column, the `rental_asset_seals` history table, and immutable issue/return JSON snapshots on
`rental_order_items`.

Correction migration `0050_rental_asset_components` follows the current schema head
`0049_variant_sort_order`, adds `rental_asset_components` and the immutable
`component_verification_snapshot` return column.

## Physical UAT pending

Verify a no-identification issue, a serial-only item, an item with three real seals, replacement
history, agreement print, normal all-OK return and one problem return on desktop and iPhone. Check
that seal problems are visible but asset availability still follows the selected return condition.
Then configure Puzzi with Основной блок, Бак, Крышка бака, Шланг and Насадка для пола; issue it,
add another component afterward and confirm the old return still shows only the issue snapshot.
Mark Насадка missing, provide a note and verify INCOMPLETE routes the asset to existing maintenance.

Condition photos remain a separate existing concept: components answer what must be returned,
seals show whether controlled parts were opened/replaced, and photos show physical condition.
