# Current Sprint

## Sprint 8 — Workflow UX

### Goal

Turn the completed catalog, media, receipt, inventory and Ready for Sale capabilities into one fast mobile-first employee workflow.

The first daily workflow starts with identification. A known Variant begins with a scan or search; a new Product or Variant must begin with a photo. Both paths take the delivered item to a posted receipt and a clear Ready for Sale state.

### Product principles

- [x] Photo First: a new physical product begins with its first photo
- [x] Repeat deliveries reuse the existing Variant photo; a new delivery photo is optional
- [x] Process over CRUD: employees work through a guided workflow, not admin tables
- [x] Mobile First: the primary intake path must work comfortably from a phone
- [x] Every Action Has an Author
- [x] Everything Is Measurable
- [x] API First: the workflow remains independent from a specific frontend
- [x] Rental remains a first-class future lifecycle

### Discovery and design

- [x] Describe the new-product mobile intake journey step by step
- [x] Describe the repeat-delivery journey for an existing Variant
- [x] Decide photo policy: mandatory for a new Product or Variant, optional for repeat delivery
- [x] Define draft ownership, resume and safe abandonment rules
- [x] Define when the first photo is stored and linked
- [x] Define workflow progress and validation without a generic BPM engine
- [x] Define employee timing and activity events without invasive surveillance
- [x] Define the Ready for Sale work queue and attention reasons
- [x] Define the minimal first-party mobile interface boundary

### First product outcome

- [x] Start new-item intake from the phone camera or photo library
- [x] Start repeat delivery by barcode scan, SKU or Variant search
- [x] Prevent a new physical product from proceeding without a photo
- [x] Select an existing Variant or create Product and Variant data
- [x] Capture supplier, quantity and purchase price
- [x] Post the receipt through the existing inventory ledger
- [x] Show readiness immediately after posting
- [x] Preserve actor attribution across the entire workflow
- [x] Resume an interrupted draft safely

### Implementation slice 1 — Resumable intake foundation

- [x] Add employee-owned `IntakeSession` and `IntakeItemDraft` persistence
- [x] Start and resume a draft before Supplier selection
- [x] Add a known Variant by exact barcode or Variant ID without requiring a new photo
- [x] Start a new Product or Variant draft only from a validated source photo
- [x] Persist quantity, purchase price and late Supplier selection progressively
- [x] Derive exact missing requirements instead of storing duplicate workflow state
- [x] Attribute sessions, item drafts and source images to the authenticated employee
- [x] Preserve explicitly abandoned sessions and items with a reason
- [x] Keep drafts inventory-neutral until atomic completion
- [x] Cover ownership, Photo First, repeat delivery, resume and abandonment through API tests

### Implementation slice 2 — Atomic completion

- [x] Create Product and Variant records for complete new-item drafts
- [x] Assign each mandatory first photo as the new Variant's primary image
- [x] Create and post one Receipt from the active session items
- [x] Complete catalog, Receipt, ledger and IntakeSession changes in one transaction
- [x] Return Receipt and Ready for Sale results from the completion command
- [x] Prove rollback and idempotency behavior with integration tests

### Implementation slice 3 — First-party workflow interface

- [x] Build the phone-first start/resume screen
- [x] Offer scan/search and photograph-new-item as the two primary actions
- [x] Show the existing primary photo after a successful barcode match
- [x] Guide the employee using the API's derived missing requirements
- [x] Show completion and Ready for Sale follow-up without exposing CRUD internals

### Operational visibility

- [x] Add an employee-facing activity feed read API
- [x] Add an employee-facing Ready for Sale attention queue read API
- [x] Record workflow start, completion, cancellation and duration
- [x] Keep audit history separate from operational metrics

### Verification

- [x] Run the complete automated suite: 218 tests
- [x] Verify authenticated source-image delivery and first-party client assets
- [ ] Complete one controlled phone smoke test through Docker and Angie

### Sprint boundary

Sprint 8 does not add a general-purpose workflow engine, sales documents, rental contracts, direct XPrinter USB printing or marketplace synchronization.

### Following business sprint

Sprint 9 — Rental Foundation:

- Asset and Asset Code;
- checkout and return lifecycles;
- before/after condition photos;
- seals, maintenance and damage history;
- rental pricing and deposits.
