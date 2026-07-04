# Architecture Decisions

## ADR-0001

### Primary Keys

Status: Accepted

Date: 2026-07-04

### Decision

All database entities use UUIDv7 as the primary key.

UUIDs are internal identifiers and must not be exposed to end users.

### Human-readable identifiers

Where required, separate business identifiers are used:

- SKU
- Barcode
- Asset Code (REN-000001)
- Receipt Number
- Rental Number

These identifiers are independent from the database primary key.

### Rationale

Reasons:

- Safe integration with external systems.
- Easy data import/export.
- Future synchronization between databases.
- Better scalability.
- Stable public identifiers.

### Consequences

Developers should never use sequential integers as entity identifiers.

Users should never see UUID values in the interface.

## ADR-0002

### Deletion policy

Status: Accepted

Date: 2026-07-04

### Decision

Most business entities use soft delete through `deleted_at`.

Hard delete is allowed only as an explicit administrator action.

Hard delete must not be the default behavior in UI, API or services.

### Soft delete

Soft delete sets:

- `deleted_at`
- `deleted_by`, if user context is available

Soft-deleted records are hidden from normal lists.

### Restore

Soft-deleted records can be restored by administrator.

### Hard delete

Hard delete is allowed only from an administrator menu or maintenance command.

Before hard delete, the system must check references.

Entities with stock movements, receipts, rentals, publications or audit history should not be hard-deleted automatically.

### Rationale

Soft delete protects business history.

Hard delete is needed for test data, mistaken imports and technical cleanup.

## ADR-0003

### Deleted media policy

Status: Accepted

Date: 2026-07-04

### Decision

When an entity is soft-deleted, media metadata remains in the database.

For product images, the system should keep a small thumbnail even if original images are later removed.

### Image versions

Core stores image versions:

- original
- web
- thumb

### Soft-deleted records

For soft-deleted records:

- image metadata remains;
- thumbnail remains;
- web version may remain;
- original may be archived or removed later by cleanup job.

### Hard-deleted records

For hard-deleted records:

- database references are removed only after safety checks;
- original and web images may be deleted;
- thumbnail can be kept in audit/archive storage if the deleted entity had business history.

### Rationale

Thumbnails help visually identify deleted products without storing large media forever.

## ADR-0004

### Product status model

Status: Accepted

Date: 2026-07-04

### Decision

Core does not use one universal product status for everything.

Statuses are separated by responsibility:

- validation status
- publication status
- deletion state
- rental asset status

### Validation status

Used for CatalogProduct / CatalogVariant readiness.

Possible values:

- draft
- ready

`draft` means the entity is incomplete.

`ready` means required business data exists.

### Publication status

Stored in Publication.

Possible values:

- draft
- pending
- published
- failed
- disabled

Publication status is channel-specific.

Example:

One Variant can be:

- published in AQSI
- draft in Tilda
- failed in Telegram

### Deletion state

Soft delete is controlled by `deleted_at`.

If `deleted_at` is not null, the entity is considered deleted.

### Rental asset status

RentalAsset has its own status.

Possible values:

- available
- rented
- maintenance
- damaged
- retired
- lost

### Rationale

One generic `status` field mixes unrelated business concepts.

Separate statuses make the system easier to understand, extend and debug.