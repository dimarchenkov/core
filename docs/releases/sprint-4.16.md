# Sprint 4.16 — Rental Operations Workspace & Issue

**Status:** implemented; automated verification complete; physical UAT pending.

**Epic 4 status:** ✅ Operational milestone completed; active follow-up in progress.

## Operational workspace

`/rent` is now the Rental operational root. Its default view puts active rentals and orders
waiting for issue first; history is available without becoming the dominant screen. READY cards
show every physical item, tariff, rental total, recommended deposit and explicit Open/Issue
actions. They never show a countdown.

The READY order detail continues the same workflow in place: immediately below «Итоговые условия
заказа» a no-agreement order shows the primary «Выдать оборудование» action. It opens the exact
same issue confirmation used by the workspace card. An agreement-required detail shows the
Sprint 4.17 blocker instead of an action, so the additional entry point cannot bypass policy.
After confirmation the shared flow opens the active order detail with item-level timing; reload
of its order route restores that operational detail.

ACTIVE cards preserve item-level truth: every issued item shows its own asset number, fixed
`due_at`, countdown and optional grace note. Returned siblings remain visible as returned. Open
and Return are first-class actions; no generic direct-completion action bypasses inspection.

## Issue semantics

The existing `RentalOrderService.issue` workflow remains the single issue implementation. It
locks the order and affected `RentalAsset` rows, revalidates every asset, transitions all items
and assets in one transaction, and rolls the whole operation back if one sibling cannot issue.
Asset locks are acquired in stable id order. An already-issued order makes a repeated identical
request idempotent instead of duplicating transitions or audit facts.

Normal issue uses one current server timestamp for the whole order. The confirmation screen
shows all items and lets the operator use that fast default or reveal a secondary manual time.
Manual time must be timezone-aware, no more than 24 hours old and no more than one minute in the
future. Browser time is only used for a visual preview; the default persisted time is server
authoritative.

- DURATION sets `issued_at` to actual issue and derives `due_at` from the snapshotted
  `duration_minutes`, never from the current live tariff.
- SLOT sets actual `issued_at` but retains its selected-date fixed slot end. Normal issue is
  blocked when that end has already passed.
- Every successfully issued asset uses the existing `RENTED` availability state.
- Order-level and item-level issue facts retain the issuing operator. Asset audit keeps the same
  operator through the existing update fields.

## Agreement boundary

Temporary strategy **A** is intentional: if any item snapshot has `requires_agreement=true`, the
confirmation explains that an agreement is required and issue is blocked until Sprint 4.17.
There is no hidden or administrator bypass and Core does not pretend an agreement exists.
Orders whose snapshots do not require an agreement use the same normal READY → issued workflow;
there is no separate simplified-rental domain.

## Timers and overdue

Countdown values are never persisted. The browser recalculates `due_at - Date.now()` every
second, so reload, background throttling and sleep recover from factual timestamps. Before the
boundary it displays remaining time; from `due_at` onward it displays an upward-counting overdue
duration. Grace is shown separately and does not postpone operational lateness. No overdue money
is calculated in this sprint.

Cards use stacked item blocks rather than wide tables. On narrow screens item facts, timers and
48px primary actions stack into one column; issue confirmation uses the same mobile-safe layout.

## Verification

Focused coverage includes server-time and manual issue, snapshot-based DURATION due time, fixed
and expired SLOT behavior, multi-item atomicity, unavailable sibling rollback, asset lifecycle,
agreement blocking, idempotency, operator audit, workspace actions, per-item timer markup,
overdue/grace separation and mobile controls. Full results are recorded in the implementation
handoff; physical acceptance remains deliberately unclaimed.

## Physical UAT

Stop after deployment and verify without changing production facts unnecessarily:

1. **DURATION:** leave a five-hour test order READY for several minutes. Confirm no timer. Issue
   with “Сейчас”; verify actual `issued_at`, `due_at` five hours later, a running timer and a
   `RENTED`/unavailable asset.
2. **Multi-item:** issue Puzzi five hours plus Bosch three hours together. Verify the same actual
   issue time, distinct item due times and one active order.
3. **Manual time:** issue a no-agreement test order with a time ten minutes earlier. Verify its
   due time derives from that overridden actual time.
4. **SLOT:** issue a 09:00–14:00 test slot after its start but before its end. Verify due remains
   14:00. A normal attempt after 14:00 must be blocked with a clear message.
5. **Overdue:** use safe test data around a due boundary. Verify “Осталось” before it and
   “ПРОСРОЧЕНО” counting upward immediately after it; grace must not delay that switch.
6. **Mobile:** on a phone verify READY, ACTIVE, multi-item rows, readable timer, Issue and Return
   buttons, confirmation layout and absence of horizontal scrolling.

Sprint 4.17 remains responsible for the Rental Agreement and PDF. Sprint 4.18 remains responsible
for overdue charging and any return redesign.
