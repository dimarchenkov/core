# Sprint 4.15 — Rental Draft & Tariff Snapshot

**Status:** implemented; multi-item manual UAT pending.

**Epic 4 status:** ✅ Operational milestone completed; active follow-up in progress.

## Multi-item domain boundary

Sprint 4.15 reuses the existing aggregate and does not introduce a second Rental model:

- `RentalOrder` owns the Customer snapshot, overall lifecycle, actual/agreed order-level deposit,
  derived totals and the future single agreement;
- every `RentalOrderItem` owns one physical `RentalAsset`, exact tariff version, immutable tariff
  snapshot, agreed price, planned rental date, eventual actual issue/due time, grace, recommended
  deposit and agreement flag.

One order may contain Puzzi and Bosch with different tariffs and return times. Two units of the
same Variant are two items with different physical asset ids. The same asset cannot appear twice.
Legacy order-level planned boundaries remain only as compatibility/summary values. Item-level
`planned_rental_date`, `issued_at` and `due_at` carry the operational truth.

## Client-first workflow

The operator selects an existing Customer, opens an incomplete `DRAFT`, and repeatedly uses
«Добавить оборудование». Each item independently selects an available asset, its current tariff,
and tariff or manual timing. Saving an item returns to the order and preserves all siblings.
Prepared items can be edited or removed independently.

Saving does not issue equipment, change an asset to `RENTED`, or generate an agreement/PDF.

## Timing and snapshots

- DURATION while DRAFT/READY: only `planned_rental_date` is known; `issued_at` and `due_at` are
  null. Actual issue starts the clock and computes `due_at = issued_at + duration_minutes`.
- SLOT: selected date plus tariff `slot_start`/`slot_end` in `CORE_RENTAL_TIMEZONE`.
- SLOT actual issue does not shift the fixed slot end.
- MANUAL remains an explicit exceptional aware start/end; the reusable tariff is unchanged.

Each item freezes tariff lineage/id/version, name/type/base price, agreed price and override flag,
duration or slot boundaries, grace, deposit, agreement flag, canonical terms, timing source and
planned date. Actual issue timestamps are operational item facts, not tariff mutations. Later
tariff versions or archive operations do not rewrite snapshots.

## Derived order facts and READY

`rental_total` is the Decimal sum of item agreed prices minus existing discounts.
`recommended_deposit_total` is the sum of item snapshot recommendations. `deposit_amount` remains
the separate order-level actual/agreed deposit fact and is not overwritten by recommendations.
`requires_agreement` is true when any item snapshot requires it; it is not a duplicated editable
flag.

An order is `READY` only when it has at least one item and every item has complete asset, tariff,
snapshot, pricing and planned-date facts. A normal DURATION item does not need actual issue or due
time for READY. One incomplete legacy item keeps it `DRAFT`; removing the last item returns it to
`DRAFT`. Agreement/PDF is not a READY prerequisite in this sprint.

## Migration and compatibility

Migration `0040_multi_item_snapshots` adds nullable tariff/timing fields to
`rental_order_items`, copies the accidental 4.15 order-level snapshot into the sole item of each
existing tariff draft, then removes those accidental order-level fields. Historical non-tariff
items remain valid and nullable. Existing multi-item issue and partial-return behavior is
unchanged.

Migration `0041_duration_issue_timing` adds item `planned_rental_date` and `issued_at`. Existing
prepared DURATION items retain their selected date while the prematurely derived start/due values
are cleared. SLOT boundaries and commercial snapshot facts are preserved.

Future Sprint 4.17 normally creates one agreement for the whole `RentalOrder` and renders all
item snapshots in `rental_items`.

## Manual UAT

After applying migration `0040_multi_item_snapshots`, stop and verify:

1. Create one Rental for a real/test Customer and add Puzzi with «5 часов».
2. Use «Добавить оборудование» and add a different Bosch asset with «3 часа».
3. Confirm DURATION cards show only the issue date and say that the term starts upon physical
   issue; leaving the order READY must not consume rental time.
4. Confirm a SLOT item still shows its fixed start/end and actual issue will not shift its end.
5. Edit only Bosch and confirm the Puzzi snapshot/timing is unchanged.
6. Remove Bosch and confirm Puzzi remains; add Bosch again and confirm the order is `READY`.
7. Attempt to add the same RENT code again and confirm the backend rejects it.
8. Confirm no asset became `RENTED`, no issue occurred, and no agreement/PDF was generated.
