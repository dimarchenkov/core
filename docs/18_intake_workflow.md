# Intake Workflow

## Purpose

Sprint 8 turns the existing catalog, media, receipt and inventory capabilities into one resumable employee workflow.

`IntakeSession` is a temporary operational workspace. It is not a warehouse document and does not affect stock. A `Receipt` is created only when the employee has supplied enough information to complete the intake.

## Human-first entry

The workflow does not begin with Supplier selection.

The employee opens Intake and chooses the natural identification action:

- scan or search for a known Variant;
- take a photo for a new Product or Variant.

Supplier, quantity and prices are requested after the physical item has been identified. Supplier remains mandatory before completion because one completed IntakeSession produces exactly one Receipt.

## Photo policy

### New Product or Variant

The first persisted fact is a valid source image. Core creates the new item draft and stores the source image in one operation. Product and Variant fields can be filled afterwards, but the new catalog object cannot be created without that image.

### Repeat delivery

The employee scans a barcode or searches for an existing Variant. Core shows its current primary image for visual confirmation.

A new delivery photo is optional. Repeat delivery must not force the employee to photograph an already identifiable catalog position.

## Domain model

### IntakeSession

One employee-owned, resumable intake workspace.

Fields:

- `id`;
- `status`: `draft`, `completed` or `abandoned`;
- nullable `supplier_id`;
- nullable `receipt_id`, assigned only after successful completion;
- `created_by_id` as the session owner;
- `created_at`, `updated_at`;
- nullable `completed_at` and `abandoned_at`;
- nullable abandonment reason.

Rules:

- a user may have several drafts and can resume any owned draft;
- only the owner may mutate a draft in the first implementation;
- completed and abandoned sessions are immutable;
- one completed session creates one Receipt for one Supplier;
- a session never changes inventory directly.

### IntakeItemDraft

One incomplete or completeable position inside an IntakeSession.

Kinds:

- `existing_variant`;
- `new_variant` for an existing Product;
- `new_product` with its first Variant.

Common fields:

- `session_id`;
- `kind`;
- nullable `variant_id` for a known position;
- nullable `product_id` for a new Variant of an existing Product;
- nullable `image_id`;
- new Product and Variant input fields;
- quantity;
- purchase price;
- timestamps and actor attribution.

Draft completeness is derived from its kind and fields rather than maintained as a second mutable readiness status.

Required facts:

| Kind | Required before completion |
| --- | --- |
| `existing_variant` | active Variant, quantity, purchase price |
| `new_variant` | existing Product, source image, Variant title, quantity, purchase price |
| `new_product` | source image, category, Product title, Variant title, quantity, purchase price |

## Lifecycle

```text
draft
  ├── complete successfully → completed
  └── abandon explicitly   → abandoned
```

There is no generic workflow engine. The application service validates explicit IntakeSession rules and returns derived missing requirements for both the session and each item.

## Completion transaction

Completion requires:

- an active Supplier;
- at least one non-abandoned item;
- every item to have all facts required by its kind;
- all referenced Product, Variant and Image records to remain valid.

Core then performs one transaction:

1. create Product and Variant records for new catalog positions;
2. assign the mandatory image as the new Variant's primary image;
3. create one Receipt and its ReceiptItems;
4. post the Receipt through the existing immutable inventory ledger;
5. link the resulting Receipt to the IntakeSession;
6. mark the IntakeSession completed.

If any step fails, catalog, Receipt and inventory changes roll back together. The IntakeSession remains a resumable draft with its previously stored images and user input.

## Resume and abandonment

- every successful field change is persisted immediately;
- the employee can list and reopen owned draft sessions;
- opening a draft shows missing requirements instead of guessing a current screen;
- abandonment is explicit and attributed;
- abandoned data and source images are retained in the first implementation for audit and recovery;
- retention and orphan-media cleanup become a separate operational policy later.

## API boundary

The first API should support:

- create, list and read owned IntakeSessions;
- set or change Supplier while the session is a draft;
- create a new-item draft from an uploaded photo;
- create an existing-item draft from barcode or Variant ID;
- edit item fields;
- obtain derived session and item missing requirements;
- abandon an item or session explicitly;
- complete the session atomically;
- return the resulting Receipt and Ready for Sale results.

All commands are authenticated and attributed. The API does not depend on React, SQLAdmin or another client.

### Implemented workflow API

The first implementation slice provides persistent owned sessions, progressive item editing,
late Supplier selection, Photo First upload for new catalog positions, exact barcode or Variant
identification for repeat delivery, derived missing requirements, resume and explicit abandonment.

Draft operations intentionally do not create Product, Variant, Receipt or inventory Movement
records. The explicit completion command locks the owned session, validates every active item,
materializes new catalog positions and primary image links, creates and posts one Receipt, derives
Ready for Sale results and marks the session completed in one transaction.

Completion is idempotent: retrying an already completed session returns its original Receipt and
Variant mappings. A failure at any point rolls back catalog, Receipt and ledger changes while the
persisted draft, its source photos and entered data remain available for correction and retry.

## Minimal mobile interface

The first-party interface needs only these workflow screens:

1. start or resume Intake;
2. choose `Scan known item` or `Photograph new item`;
3. fill the short item form;
4. review session positions and select Supplier;
5. complete Intake;
6. view Receipt and Ready for Sale follow-up actions.

Catalog and Receipt cards remain reference and history views, not the employee's primary workplace.

## Outside the first implementation

- a general-purpose workflow or BPM engine;
- collaborative editing and session transfer between employees;
- automatic image recognition or OCR;
- supplier inference;
- mixed-Supplier completion inside one session;
- sales, returns and rental checkout;
- direct printer control.
