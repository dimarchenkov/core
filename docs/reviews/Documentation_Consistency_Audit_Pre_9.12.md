# Documentation Consistency Audit — before Sprint 9.12

Audit date: 2026-09-24. This report records the pre-cleanup state. It is intentionally preserved
as audit evidence; subsequent documentation edits do not turn it into a current source of truth.

## CRITICAL contradictions

| Class | File / section | Current statement | Conflicting/current fact | Proposed treatment |
| --- | --- | --- | --- | --- |
| A, B, F | `docs/14_product_identifiers.md`; `docs/04_architecture.md` / Catalog; `docs/17_aqsi_publication.md` / mapping; `planning/backlog.md` / Epic 9; `docs/architecture_review/10_decisions.md` / Sprint 9.11 | A Variant keeps an immutable internal EAN plus append-only manufacturer codes; AQSI selects a preferred manufacturer code with internal fallback; several barcodes can be active. | A Variant has exactly one replaceable current operational barcode. Its origin is EXTERNAL or INTERNAL. Old values are history, not lookup aliases; AQSI receives the same current code. Code and tests already implement current-only lookup/replacement, while some compatibility names and history storage remain. | Make `docs/14_product_identifiers.md` canonical, rewrite current architecture/AQSI/roadmap wording, and mark the older Sprint 9.11 decision as superseded history. Explicitly record the legacy implementation literal `manufacturer` as terminology debt for EXTERNAL. |
| A, B, H | `docs/16_labels.md`; `docs/04_architecture.md` / Product labels; `planning/backlog.md` / Epic 2; Sprint 8 architecture snapshots | Product label 58×40 is authoritative and still awaits physical calibration/direct printing. | The canonical Product label is the physically accepted 40×30 mm vector EAN-13 template: X-dimension 0.300 mm, 95 modules = 28.5 mm, quiet zones 9 modules, total 33.9 mm. Direct remote CUPS/Xprinter printing is validated. 58×40 remains compatibility/history and is also independently relevant to RentalAsset label profiles. | Rewrite the Product label document and active architecture/roadmap. Preserve 58×40 only as a historical/compatibility fact; do not change RentalAsset label formats. |
| A, F | `planning/current.md` / Definition of Done and `planning/backlog.md` / Sprint 9.12 | Sprint 9.12 promises an AQSI `drift/not-synchronized` state. | 9.12 can show the current locally known publication/sync state. True remote drift detection requires Sprint 7.13 reconciliation. | Replace “drift” with locally known publication/out-of-date/failure state and reserve reconciliation drift for 7.13. |
| G, H | `planning/backlog.md` / Epic 2, Epic 3, Epic 4; `docs/architecture_review/10_decisions.md` status table | Epic 2 and 3 are “In Progress”; Epic 4 is “Completed” while required-looking tariffs/metrics remain unchecked; Sprint 9 and 10 are still “In progress”. | Ready-for-Sale and Intake operational milestones are delivered; remaining items are follow-up debt. Rental’s delivered milestone is complete, while tariffs and operational metrics were not required for that milestone and remain explicitly deferred. Sprint 9/10 implementation work is completed. | Mark Epic 2/3 milestones completed with explicit follow-up sections; keep Epic 4 completed but reclassify unchecked items as deferred follow-up; correct the decision-log status table. |

## IMPORTANT stale or rudimentary items

| Class | File / section | Current statement | Conflicting/current fact | Proposed treatment |
| --- | --- | --- | --- | --- |
| E, H | `planning/current.md` / Sprint 9.11 baseline | Detailed completed Sprint 9.11 UAT dominates the current-work document; HEIC and autosave headings say “UAT pass” despite remaining physical/authenticated checks. | Current work is Sprint 9.12. Historical evidence is useful, but the pending iPhone/browser checks must not be represented as passed. | Move detailed evidence to a Sprint 9.11 release/history note; keep only a concise validated baseline and honest remaining debt in `current.md`. |
| C, G | `planning/backlog.md` / Intake validated scope | “Every Product has a Variant” and the implemented Variant fields remain unchecked. | Intake supports Product with one or multiple Variants, reserved identifiers, photos, quantity, purchase/retail price and rental allocation. Full rental tariff rules and physical camera acceptance remain incomplete. | Check only implementation-proven scope; split genuinely incomplete tariff/mobile acceptance items. |
| B, H | `docs/05_mvp.md` | Original MVP scope, priorities and absent Tilda CSV import are written as current commitments. | The document is a historical planning baseline; Rental and later Intake work are already delivered, while Tilda import remains future. | Add an explicit historical-status banner and point to current/backlog sources rather than rewriting history. |
| D, H | `README.md`; `docs/02_modules.md` | Tilda work/import-export/media processing are listed as current capabilities; Pricing history is “later”. | AQSI manual publication, Pricing history and Rental are current; Tilda sync/import and several media/import outputs remain planned. | Correct capability/status wording. |
| A, B | `docs/ai/AGENTS.md`; `docs/ai/DECISIONS.md` / ADR-0004 | Variant “owns stock”; validation has stored `draft/ready` status. | Quantity belongs to Variant only through immutable Inventory movements; Ready for Sale is derived and not persisted. | Correct agent rules and mark the validation-status portion of ADR-0004 superseded while retaining publication/deletion/Rental decisions. |
| H | `docs/architecture_review/01_*` through `05_*` and Sprint 8 review set | Old snapshots use “current” and contain facts such as Rental not implemented or Product label 58×40. | These were valid at their stated baselines but are no longer authoritative. | Add prominent historical-snapshot notices; do not erase findings or retroactively rewrite evidence. |
| B | `docs/09_receipts.md` / Catalog items | A new Variant is created in Intake, then the user returns to a separate Receipt. | Normal Intake completion already creates/posts the Receipt in one workflow; direct Receipt commands only attach existing Variants. | Rewrite the boundary without changing the valid rule that Receipt does not create Catalog entities. |
| B, H | `docs/03_user_story_map_rental.md`; `docs/domain/customers_and_rental_orders.md`; `docs/03_business_processes_rental.md` | Implemented UI/economics are described as future; old proposed field names and unresolved revenue timing are presented as current. | Rental UI, history and economics were delivered in Sprint 10E–10G; current names are `agreed_price`, `charged_amount`, `completed_by`; revenue is recognized from completed returned/lost items. | Update current domain wording and identify truly deferred tariff/allocation decisions. |
| F | `docs/00_glossary.md` / Customer | Customer is defined only as a rental recipient. | Customer is a reusable business entity for Rental, future Sales/Loyalty, purchase history and customer service. | Broaden the glossary definition; retain `Customer != User`. |
| B | `docs/07_user_flows.md` / Sale | A sale is merely “a document or external fact”. | Target Sales owns Cart/Sale/SaleItem, customer/discount snapshots, payment/fiscalization state and emits immutable SALE movements after success. | Replace the rudimentary overview with the accepted POS boundary and scan flow. |

## LOW-RISK cleanup

| Class | File / section | Current statement | Conflicting/current fact | Proposed treatment |
| --- | --- | --- | --- | --- |
| E | `planning/backlog.md` / Epic 8 Infrastructure | “Update and recovery guide” is duplicated verbatim. | One roadmap item is sufficient. | Remove the duplicate. |
| F | `docs/18_intake_workflow.md` / client capabilities | “multi-barcode input” can be read as several active barcodes. | The UI accepts one scanned operational code in several supported formats/input adapters. | Use “barcode input supporting multiple formats/adapters”. |
| E | Active docs linking identifiers, labels and AQSI | Canonical rules are repeated independently. | `docs/14_product_identifiers.md`, `docs/16_labels.md`, and `docs/17_aqsi_publication.md` can be canonical per concern. | Keep concise boundary statements elsewhere and link to the canonical documents. |

## UNCERTAIN — requires product decision

| Topic | Evidence | Decision left open |
| --- | --- | --- |
| AQSI archive semantics | API capability is not yet confirmed in the automatic-sync design. | Whether Sprint 7.13 removes or deactivates an archived Core item in AQSI. |
| AQSI stock synchronization | Current adapter publishes card/price, not stock. | Whether stock is ever synchronized and under what independent contract. |
| Sales offline/cloud failure | Both validated payment paths depend on AQSI Cloud API and Internet. | Checkout behavior when AQSI/cloud is unavailable; no fallback is assumed. |
| Sales failure recovery | The spike is process-local and non-durable. | Durable duplicate protection, unknown outcomes, acquiring-success/fiscalization-failure, refunds/reversals and supported payment methods. |
| Rental physical-loss ledger | Rental can mark LOST/RETIRED without an Inventory decrease. | Exact compensating/withdrawal movements for physical loss and retirement. |
| Rental tariff policy | Basic agreed/charged amounts and economics exist. | Duration tariffs, calculation rules and allocation of order-wide discounts/delivery. |
| Customer phone uniqueness | Current domain document explicitly leaves it unresolved. | Unique normalized phone versus indexed, warning-only duplicate handling. |
| HEIC redistribution and 48 MP | Functionality is implemented; licensing and resource limits remain explicit. | External redistribution obligations and any future increase of the 20 MP limit. |

## Audit boundary

- Product/application code, schema, migrations and tests are evidence only and are not to be edited.
- Existing uncommitted work predates this audit and must be preserved.
- Cleanup should remain focused; historical release/UAT facts should be labelled, not deleted.

## Completion record

All 17 meaningful finding groups above were treated: 4 critical contradictions, 10 important
stale/rudimentary groups and 3 low-risk cleanup groups. The 8 topics in **UNCERTAIN** remain
explicit product/architecture decisions; the documentation does not invent answers for them.

The audit changed documentation only. It did not change application code, tests, migrations or
schema, and it did not create a commit. Pre-existing uncommitted work was preserved.

Validation after cleanup:

- current identifiers, Product labels, Intake, AQSI, Sales/POS, Customer/Loyalty and Rental
  boundaries were searched globally for contradictory wording;
- local Markdown links resolve;
- `git diff --check` passes;
- no product test suite was run because the audit changed documentation only.

## Documentation changed by this audit

The focused cleanup touched 31 documentation files (29 existing files and 2 new audit/history
artifacts):

- `README.md`;
- `docs/00_glossary.md`, `docs/01_vision.md`, `docs/02_modules.md`,
  `docs/03_business_processes.md`, `docs/03_business_processes_rental.md`,
  `docs/03_user_story_map_rental.md`, `docs/04_architecture.md`, `docs/05_mvp.md`,
  `docs/06_domain_rules.md`, `docs/07_user_flows.md`, `docs/09_receipts.md`,
  `docs/13_pricing.md`, `docs/14_product_identifiers.md`, `docs/15_ready_for_sale.md`,
  `docs/16_labels.md`, `docs/17_aqsi_publication.md`, `docs/18_intake_workflow.md`;
- `docs/ai/AGENTS.md`, `docs/ai/DECISIONS.md`;
- `docs/architecture_review/01_module_map.md`, `docs/architecture_review/02_service_map.md`,
  `docs/architecture_review/03_transaction_map.md`,
  `docs/architecture_review/04_domain_model.md`,
  `docs/architecture_review/05_architecture_backlog.md`,
  `docs/architecture_review/10_decisions.md`;
- `docs/domain/customers_and_rental_orders.md`;
- `planning/backlog.md`, `planning/current.md`;
- new `docs/releases/sprint-9.11.md` and
  `docs/reviews/Documentation_Consistency_Audit_Pre_9.12.md`.

Some of those files already contained uncommitted user changes. The audit preserved them and made
only focused documentation edits around the audited contradictions. Other pre-existing dirty
application/test files and generated PDFs were not modified by the audit.

## Documentation set inspected

The following 59 Markdown files were inspected or, for the two audit outputs, generated and then
validated:

- `.codex.md`, `README.md`;
- `docs/00_glossary.md`, `docs/00_product.md`, `docs/00_product_philosophy.md`;
- `docs/01_principles.md`, `docs/01_vision.md`, `docs/02_modules.md`,
  `docs/02_user_journeys.md`;
- `docs/03_business_processes.md`, `docs/03_business_processes_rental.md`,
  `docs/03_user_story_map_rental.md`, `docs/04_architecture.md`, `docs/05_mvp.md`,
  `docs/06_domain_rules.md`, `docs/07_user_flows.md`, `docs/08_identity.md`,
  `docs/09_receipts.md`, `docs/10_inventory.md`, `docs/11_deployment_bootstrap.md`,
  `docs/12_product_documentation.md`, `docs/13_pricing.md`,
  `docs/14_product_identifiers.md`, `docs/15_ready_for_sale.md`, `docs/16_labels.md`,
  `docs/17_aqsi_publication.md`, `docs/18_intake_workflow.md`,
  `docs/19_operational_visibility.md`;
- `docs/ai/AGENTS.md`, `docs/ai/CONVENTIONS.md`, `docs/ai/DECISIONS.md`,
  `docs/ai/PROMPTS.md`, `docs/ai/WORKFLOW.md`;
- `docs/aqsi-sale-spike.md`, `docs/direct-cups-printing.md`, `docs/heic-ingestion.md`,
  `docs/domain.md`, `docs/domain/customers_and_rental_orders.md`;
- `docs/architecture_review/01_module_map.md`, `docs/architecture_review/02_service_map.md`,
  `docs/architecture_review/03_transaction_map.md`,
  `docs/architecture_review/04_domain_model.md`,
  `docs/architecture_review/05_architecture_backlog.md`,
  `docs/architecture_review/06_sprint_08_conformance.md`,
  `docs/architecture_review/07_product_readiness.md`,
  `docs/architecture_review/08_engineering_state.md`,
  `docs/architecture_review/09_findings.md`, `docs/architecture_review/10_decisions.md`,
  `docs/architecture_review/ADR/ADR-002-transaction-ownership.md`,
  `docs/architecture_review/ADR/ADR-003-workflow-layer.md`;
- `docs/releases/sprint-9.11.md`, `docs/releases/v0.4.0.md`,
  `docs/releases/v0.5.0.md`, `docs/releases/v0.6.0.md`, `docs/releases/v0.7.0.md`;
- `docs/reviews/Documentation_Consistency_Audit_Pre_9.12.md`,
  `docs/reviews/Sprint_08_Audit.md`;
- `planning/backlog.md`, `planning/current.md`.
