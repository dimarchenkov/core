# Architecture Review v2 — Findings after Sprint 8

This document records findings only. It intentionally does not prescribe implementation or
refactoring.

## Blockers

No blocker was found for discussing Sprint 9. Runtime migration application was not reconfirmed
because Docker was unavailable, so this is not a production deployment certification.

## High

### H-01 — Sprint 8 ActivityEvent is missing from Alembic target metadata

`ActivityEvent` is mapped on shared `Base` (`src/core/activity/models.py:17-20`), and migration
`0016_create_activity_events` creates `activity_events`
(`migrations/versions/0016_create_activity_events.py:22-62`). However,
`migrations/env.py:8-16` imports Catalog, Identity, Intake, AQSI, Inventory, Media, Pricing,
Receipt and Supplier models but not `core.activity.models`.

Impact: Alembic history has a valid single head, but metadata-based drift detection or a future
autogenerate process does not see the Activity model and can classify the migrated table as
outside the application metadata.

## Medium

### M-01 — Transaction style remains mixed outside the Sprint 8 migration set

ADR-002 says domain services do not finalize caller-owned transactions. Catalog, Media, Pricing,
Receipt and Inventory follow the transaction-neutral style, but `SupplierService` commits inside
all three write methods (`src/core/supplier/service.py:48,83,91`). `IdentityService` also commits
inside commands (`src/core/identity/service.py:74,148`) and rolls back at line 151.

Each command still has one effective owner, so this is not a demonstrated atomicity failure. It
is partial, context-dependent conformance to the accepted transaction architecture.

### M-02 — Ready for Sale attention pagination is applied after an unbounded database read

`ReadyForSaleReadService` builds one SQL statement, iterates every matching active Variant
(`src/core/readiness/read_service.py:73-110`), derives/filter requirements in Python
(`src/core/readiness/read_service.py:111-136`), then applies `offset` and `limit` to the Python
list (`src/core/readiness/read_service.py:138-144`).

Impact: the API contract is paginated, but database work and process memory grow with the entire
active catalog. This is narrower than an N+1 defect but does not meet the previous backlog's
“bounded SQL/read-service query” completion wording.

### M-03 — No automated architecture dependency guard

The current context graph has no cycle, but there is no test encoding prohibited reverse imports.
AB-010 remains open in `docs/architecture_review/05_architecture_backlog.md`. High fan-in Catalog
and high fan-out Intake remain convention-protected.

Impact: a future reverse Readiness/Catalog or Inventory/Receipt import can bypass ADR-003 without
failing an automated check.

### M-04 — No repository CI quality gate

`.github/workflows/`, `.pre-commit-config.yaml` and `Makefile` are absent. Ruff and all 218 tests
pass locally, but no committed automation demonstrates that the same gates run for pushes or pull
requests.

### M-05 — README capability list exceeds executable product scope

README lists Tilda and Rental as main capabilities (`README.md:17-25`). No Tilda module/route/job
and no Rental model/service/route/migration are registered in the runtime
(`src/core/main.py:36-50`; `src/core/rental/__init__.py` is empty).

Impact: the entry-point documentation does not distinguish present functionality from product
direction.

## Low

### L-01 — Session lifetime remains an undocumented ORM contract

Routes, services and repositories share a request-scoped raw Session
(`src/core/database.py:11-17`), and services return ORM entities. The previous review already
records lazy-loading pressure; AB-011 remains open. No failing test demonstrated a detached-load
defect in this audit.

### L-02 — Media target validation remains Catalog-specific

`ImageLinkService` imports Catalog repositories (`src/core/media/service.py:9`) and supports
Catalog Product/Variant targets. This is consistent with the deferred AB-012 decision, but it is
a known pressure point before Rental introduces Asset/Inspection photos.

### L-03 — Legacy Intake remains a second executable command boundary

The one-shot endpoint is correctly marked deprecated (`src/core/intake/routes.py:96-110`) but
still invokes `IntakeService`, which creates Product/Variant/ImageLink outside the resumable
IntakeSession/Receipt workflow (`src/core/intake/service.py:14-59`).

No current caller defect was demonstrated. The finding is lifecycle/documentation debt under the
existing AB-005 removal gate.

### L-04 — Architecture UI path is stale

`docs/04_architecture.md` names `/mobile/intake`, while the registered first-party UI and README
use `/app` (`src/core/web/routes.py:12-15`, `README.md:36-40`).

### L-05 — mypy and coverage are not configured

`pyproject.toml` declares only pytest and Ruff in its development group (`pyproject.toml:26-30`).
No mypy configuration, coverage configuration or coverage plugin dependency is present.

## Observations

- O-01: No production context-level import cycle was found.
- O-02: Intake has the highest business fan-out by design and is located at an approved workflow
  boundary.
- O-03: Readiness remains entirely derived; no readiness table or mutable readiness status exists.
- O-04: `StockMovement` production construction occurs only in `InventoryService`
  (`src/core/inventory/service.py:66,95`).
- O-05: No SQL `begin()` or `begin_nested()` call exists; command owners finalize SQLAlchemy
  autobegin transactions.
- O-06: AQSI's multiple commits are documented workflow checkpoints, not accidental double
  commits (`src/core/integrations/aqsi/processor.py:58,185,203,224`).
- O-07: 218 tests and Ruff pass on Python 3.13.12.
- O-08: Docker Compose configuration parses, but the Docker daemon was unavailable for a clean
  migration/runtime smoke test.

## ADR assessment

| ADR | Current status | Fulfilment | New ADR implication |
| --- | --- | --- | --- |
| ADR-002 — Transaction Ownership | Accepted, current | Partial across the whole repository; fulfilled for Sprint 8 Intake composition and migrated command contexts | No replacement ADR indicated. |
| ADR-003 — Explicit Workflow Layer Without an Engine | Accepted, current | Substantially fulfilled | No replacement ADR indicated. |

Potential Sprint 9 decision scope for the main architecture discussion:

- whether Rental Asset is its own aggregate root and how checkout/return owns transactions;
- how Rental movements interact with the immutable Inventory ledger;
- which Rental entities may be ImageLink targets and where condition-photo invariants live;
- whether those decisions materially extend existing ADR-002/003 or require a focused new ADR.

No ADR was created by this audit.

## Questions for the main architecture discussion

1. Is the missing Activity model registration treated as a release-level migration-control risk
   before any Sprint 9 migration is authored?
2. Does Sprint 9 begin only after defining Asset, checkout/return and Inventory ownership, or is
   that definition itself the first Sprint 9 deliverable?
3. Will Rental reuse `StockMovement` source types, and which workflow is the sole transaction
   owner for checkout and return?
4. Are Asset and Inspection explicit allowed Media targets, and where are mandatory before/after
   photo rules owned?
5. Is the unbounded Ready for Sale attention query acceptable for the expected catalog size at
   the Sprint 9 checkpoint?
6. What is the documented removal gate and release window for the deprecated one-shot Intake
   endpoint?
7. Are CI, typing and coverage controls Sprint 9 prerequisites or separately scheduled
   engineering backlog?

