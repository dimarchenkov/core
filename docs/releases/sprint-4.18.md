# Sprint 4.18 — Rental Return & Overdue

## Implemented

- Return remains item-level and supports one selected item or every active item in one atomic
  command. `RentalOrder` closes only after all items reach a terminal outcome.
- `returned_at` comes from one server timestamp. Active overdue labels remain derived and are not
  persisted as a mutable countdown.
- The pure return calculator uses only immutable item terms: `due_at`, grace, billing period and
  snapshotted price. Grace does not shift `due_at`; the fixed tariff is never prorated.
- `NEXT_PERIOD` billing uses `ceil((overdue - grace) / tariff period)`. At the exact end of a
  billing block no extra block is added.
- Return history freezes condition, notes, overdue duration/state/charge, damage charge, deposit
  applied, operator and a JSON calculation audit. Later live tariff changes cannot change it.
- `GOOD` makes the asset available. `DAMAGED` and `INCOMPLETE` move it to maintenance. `LOST`
  retires the rented asset without deleting its identity or history.
- Deposit covers only overdue and damage/loss liabilities, never the already-paid base rental.
  Partial return shows a provisional remainder; refund and uncovered amount become final only
  when the order closes.
- The contractual `required_deposit_total` is derived from every immutable item tariff snapshot.
  The separately persisted order `deposit_amount` remains the amount actually received and is the
  only amount available for settlement. A contractual requirement never fabricates a payment.

## Interface

- `/rent` sorts active cards by chargeable overdue, grace overdue, due soon, then normal active.
- Every active item has `Оформить возврат`; the order action still supports returning all items.
- The return form shows issue/due time, grace, tariff, current lateness, full-period calculation,
  condition, notes and manual damage/compensation.
- Return and final-settlement blocks show the contractual order deposit and the actually received
  deposit as separate values. Multi-item totals always cover the whole order, including partial
  returns.
- History shows return times, base/overdue/damage amounts, deposit settlement and the immutable
  agreement link without recalculating from current tariffs.

## Migration

`0044_rental_return_overdue` adds explicit return condition, frozen overdue state/minutes/charge,
damage charge, deposit allocation and the immutable return calculation snapshot.

## Physical UAT pending

Verify on-time, in-grace, chargeable overdue, two-item partial return, damaged/incomplete/lost
asset routing, final deposit settlement, history, agreement reprint and mobile layout against real
equipment. Automated checks do not mark those physical scenarios accepted.
