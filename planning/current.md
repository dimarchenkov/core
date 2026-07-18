# Current Sprint

## Sprint 8 — Workflow UX

### Goal

Turn the completed catalog, media, receipt, inventory and Ready for Sale capabilities into one fast mobile-first employee workflow.

The first daily workflow starts with a photo and takes a physical product from an opened delivery box to a posted receipt and a clear Ready for Sale state.

### Product principles

- [x] Photo First: a new physical product begins with its first photo
- [x] Process over CRUD: employees work through a guided workflow, not admin tables
- [x] Mobile First: the primary intake path must work comfortably from a phone
- [x] Every Action Has an Author
- [x] Everything Is Measurable
- [x] API First: the workflow remains independent from a specific frontend
- [x] Rental remains a first-class future lifecycle

### Discovery and design

- [ ] Describe the new-product mobile intake journey step by step
- [ ] Describe the repeat-delivery journey for an existing Variant
- [ ] Define draft ownership, resume and safe abandonment rules
- [ ] Define when the first photo is stored and linked
- [ ] Define workflow progress and validation without a generic BPM engine
- [ ] Define employee timing and activity events without invasive surveillance
- [ ] Define the Ready for Sale work queue and attention reasons
- [ ] Define the minimal first-party mobile interface boundary

### First product outcome

- [ ] Start intake from the phone camera or photo library
- [ ] Prevent a new physical product from proceeding without a photo
- [ ] Select an existing Variant or create Product and Variant data
- [ ] Capture supplier, quantity and purchase price
- [ ] Post the receipt through the existing inventory ledger
- [ ] Show readiness immediately after posting
- [ ] Preserve actor attribution across the entire workflow
- [ ] Resume an interrupted draft safely

### Operational visibility

- [ ] Add an employee-facing activity feed
- [ ] Add an owner-facing Ready for Sale attention queue
- [ ] Record workflow start, completion, cancellation and duration
- [ ] Keep audit history separate from operational metrics

### Sprint boundary

Sprint 8 does not add a general-purpose workflow engine, sales documents, rental contracts, direct XPrinter USB printing or marketplace synchronization.

### Following business sprint

Sprint 9 — Rental Foundation:

- Asset and Asset Code;
- checkout and return lifecycles;
- before/after condition photos;
- seals, maintenance and damage history;
- rental pricing and deposits.
