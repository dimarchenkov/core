# Pricing

## Purpose

Pricing turns a received `CatalogVariant` into a commercially usable item while preserving the history of price decisions.

Pricing answers:

- what the current retail price is;
- when that price became effective;
- who assigned it;
- what earlier prices were;
- whether the price satisfies Ready for Sale requirements.

Pricing does not replace the purchase price recorded by a supplier receipt.

## Purchase price

`ReceiptItem.purchase_price` is the historical unit purchase price of a specific delivery.

It answers: "At what price did these units arrive?"

Purchase price:

- belongs to `ReceiptItem`;
- remains part of the posted receipt history;
- is not copied into `CatalogVariant` as the current price;
- is not overwritten by later deliveries;
- may later be used for cost and margin calculations.

## Retail price history

Retail prices are stored as separate immutable `Price` records linked to `CatalogVariant`.

`CatalogVariant` does not contain a direct retail price field.

Assigning a new price creates a new record. Existing price history is not edited or deleted during normal business operations.

Example:

```text
2026-08-01  retail  249.00 RUB
2026-08-15  retail  279.00 RUB
2026-09-01  retail  299.00 RUB
```

The current price is the latest applicable record for the requested price type.

## Price

MVP fields:

- `variant_id`;
- `price_type`;
- `amount`;
- `currency`;
- `effective_from`;
- `reason`;
- shared UUIDv7, timestamp and attribution fields.

## Price types

The initial implementation supports:

- `retail` — regular selling price;
- `promo` — promotional selling price.

Ready for Sale requires a valid `retail` price.

Channel-specific prices for AQSI, Tilda, Wildberries and Yandex Market are not part of the initial pricing foundation. AQSI initially receives the current retail price.

Rental pricing is not represented by this Price model. Rental later needs duration-based rates, deposits, overdue rules and other lifecycle-specific concepts.

## Money rules

- Python uses `Decimal`.
- PostgreSQL uses `NUMERIC(12, 2)`.
- The only supported MVP currency is `RUB`.
- Currency is nevertheless stored explicitly.
- Money uses `ROUND_HALF_UP` and two decimal places.
- `float` input is not accepted for money.
- Price amount cannot be negative.

A zero price may be recorded explicitly for exceptional workflows, but it does not satisfy Ready for Sale.

## Effective price

A price applies when:

```text
effective_from <= current time
```

The current price is selected deterministically by:

```text
effective_from DESC
created_at DESC
id DESC
```

Future prices may be scheduled by using a later `effective_from` value.

## Business operations

Pricing uses business actions rather than unrestricted CRUD.

Initial operations:

- `set_price`;
- `get_current_price`;
- `get_price_history`.

Authenticated HTTP endpoints:

```text
POST /api/pricing/variants/{variant_id}/prices
GET  /api/pricing/variants/{variant_id}/prices/current?price_type=retail
GET  /api/pricing/variants/{variant_id}/prices?price_type=retail
```

SQLAdmin exposes price history as read-only. Price creation goes through the business service and API so validation and actor attribution cannot be bypassed during normal work.

`set_price` must:

1. Require an active, non-deleted `CatalogVariant`.
2. Validate price type, amount and currency.
3. Create a new immutable Price record.
4. Preserve all earlier Price records.
5. Record the authenticated actor when supplied.

Normal API operations must not update or soft-delete historical Price records.

## Ready for Sale

The pricing requirement is satisfied only when the Variant has a current:

```text
price_type = retail
currency = RUB
amount > 0
effective_from <= now
```

Pricing is only one readiness requirement. Photo, SKU and barcode checks remain separate responsibilities.

## Invariants

- Purchase price remains a fact of a ReceiptItem.
- Retail and promotional prices belong to CatalogVariant through Price history.
- CatalogVariant does not store price directly.
- Price history is append-only during normal operation.
- Current price is derived, not marked by a mutable `is_current` flag.
- Price calculations never use float.
- Rental and channel-specific pricing are separate future decisions.
