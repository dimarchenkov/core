# Operational Visibility

## Purpose

Core must help an employee understand current work and help an owner understand how the shop operates. Visibility is not covert surveillance and does not replace immutable business history.

The design separates three concerns:

- domain records explain business state;
- Activity Events describe meaningful employee outcomes;
- derived metrics and work queues summarize those facts.

## What Core records

Sprint 8 records only meaningful Intake outcomes:

- `intake.session_started`;
- `intake.item_added`;
- `intake.item_abandoned`;
- `intake.session_completed`;
- `intake.session_abandoned`.

Core does not record every click, field focus, camera opening, page view or time while the application merely remains open.

## ActivityEvent

ActivityEvent is an append-only operational fact.

Fields:

- `id`;
- namespaced `event_type`;
- required `actor_id`;
- `entity_type` and `entity_id` for the workflow subject;
- `occurred_at`;
- small structured `data` payload containing only operational context.

Rules:

- events cannot be edited or deleted;
- an event is appended in the same transaction as the successful business action;
- failed transactions do not create success events;
- API secrets, passwords, image bytes and unnecessary personal data never enter event data;
- ActivityEvent is not a copy of the changed database row;
- detailed forensic auditing remains separate from the employee activity feed.

Initial event data may include item count, total quantity, Receipt ID, completion duration or an explicit abandonment reason. Values that can be derived reliably from domain records should not be duplicated without need.

## Employee activity feed

The feed answers practical questions:

- what did I complete today;
- which Intake drafts are still open;
- which session was abandoned and why;
- what should I continue next.

The first version shows the current employee's events in reverse chronological order. Owner-wide filters and aggregated dashboards can be added without changing Intake business logic.

## Owner metrics

Metrics are derived from IntakeSession, IntakeItemDraft, Receipt and ActivityEvent facts:

- sessions started, completed and abandoned;
- number of processed positions and units;
- new versus existing Variants;
- median and percentile completion duration;
- incomplete drafts by age;
- positions requiring Ready for Sale work.

Metrics are operational signals, not automatic employee performance judgments. Core should expose counts and timings with enough context to investigate process problems rather than rank people by a single number.

## Ready for Sale queue

The queue is a derived read model, not a mutable status table.

It selects active Variants that fail the existing Ready for Sale rules and returns their exact missing requirements:

- primary image;
- SKU;
- AQSI-compatible barcode;
- positive current retail RUB price.

The first queue supports:

- filtering by missing requirement;
- exact barcode and text search;
- ordering by oldest unresolved Variant first;
- direct links to the action that resolves the requirement;
- showing the primary image when available.

An intentionally inactive Variant is excluded from the employee queue. Fixing the underlying facts removes a Variant automatically; there is no manual `ready` checkbox and no dismiss action in the first version.

## Sprint boundary

Sprint 8 does not implement:

- screenshots, location tracking or background device monitoring;
- per-click analytics;
- employee rankings or productivity scores;
- a general event bus;
- a data warehouse;
- arbitrary user-defined dashboards;
- automatic disciplinary conclusions.
