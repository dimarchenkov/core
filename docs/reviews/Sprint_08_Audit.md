# Sprint 08 Architecture Audit

Audit baseline: `v0.5.0` (`49acd72`), after Sprint 8 — Workflow UX.

## Outcome

Sprint 8 materially follows the architecture established by the existing review and ADRs:

- Intake is an explicit orchestration boundary;
- Complete Intake owns one atomic SQL transaction;
- nested Catalog, Media, Receipt, Pricing and Inventory operations do not commit or rollback;
- Ready for Sale remains derived from Catalog, Media and Pricing facts;
- Inventory remains an immutable `StockMovement` ledger;
- no context-level dependency cycle was found.

The audit found no discussion blocker for Sprint 9. It found one high-severity engineering risk:
the new `ActivityEvent` model is not imported into Alembic target metadata even though migration
`0016` creates its table.

## Detailed reports

- [Sprint 8 architecture conformance](../architecture_review/06_sprint_08_conformance.md)
- [Product readiness](../architecture_review/07_product_readiness.md)
- [Engineering state](../architecture_review/08_engineering_state.md)
- [Findings and ADR assessment](../architecture_review/09_findings.md)

## Verification summary

- Ruff: passed;
- pytest: 218/218 passed;
- Alembic: one linear head at `0016_create_activity_events`;
- Docker Compose configuration: valid;
- clean PostgreSQL migration smoke test: not run because the Docker daemon was unavailable;
- mypy, coverage, pre-commit and GitHub Actions: not configured in the repository.

## Finding summary

- Blockers: none.
- High: ActivityEvent missing from Alembic target metadata.
- Medium: mixed transaction style outside migrated Sprint 8 contexts; unbounded Ready for Sale
  attention read before pagination; no architecture dependency guard; no CI quality gate; README
  overstates Tilda/Rental implementation.
- Low: undocumented Session lifetime, Catalog-specific Media targets, executable legacy Intake,
  stale `/mobile/intake` architecture path, absent mypy/coverage configuration.

## ADR status

- ADR-002 — accepted and current; fully reflected in Sprint 8 Intake composition, partially
  reflected across the entire repository.
- ADR-003 — accepted, current and substantially fulfilled.
- No ADR was created. Rental may need a focused ADR only after the main architecture discussion
  chooses aggregate, transaction, Inventory and Media ownership rules not covered by current ADRs.

## Discussion boundary before Sprint 9

The main architecture discussion should decide:

- Rental Asset and checkout/return aggregate ownership;
- transaction ownership and Inventory ledger interaction;
- Rental Media targets and condition-photo invariants;
- treatment of the Activity/Alembic metadata risk;
- whether the Ready for Sale query scale and missing engineering gates are prerequisites or
  separately scheduled work.

This audit does not implement Sprint 9 and does not propose code changes.
