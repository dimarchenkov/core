# Ready for Sale

## Purpose

Ready for Sale answers whether a `CatalogVariant` can enter the selling workflow now and, when it cannot, what work remains.

Readiness is derived from current domain facts. Core does not store a mutable universal `ready` status that could become stale.

## Requirements

A Variant is ready only when all checks pass:

- the Variant exists, is not archived and is active;
- it has an active primary image link to a non-deleted Image;
- it has a non-empty system SKU;
- it has a numeric primary barcode containing 4–22 digits;
- it has a current positive `retail` price in RUB.

Product publication and channel-specific requirements remain separate. Passing this check means the Core item is commercially complete, not that AQSI or another channel has accepted it.

## API

```text
GET /api/readiness/variants/{variant_id}/ready-for-sale
```

Example:

```json
{
  "variant_id": "019f...",
  "is_ready": false,
  "missing_requirements": [
    "missing_primary_image",
    "missing_retail_price"
  ]
}
```

Machine-readable reasons:

- `inactive_variant`;
- `missing_primary_image`;
- `missing_sku`;
- `missing_barcode`;
- `invalid_barcode`;
- `missing_retail_price`.

The response reports all current problems in stable order so mobile, web and third-party interfaces can build a work queue without duplicating Core business rules.

## Attention queue

```text
GET /api/readiness/attention
```

Query parameters:

- `requirement` — optional machine-readable reason filter;
- `search` — Product/Variant/SKU text or an exact barcode;
- `limit` — page size from 1 to 100, default 50;
- `offset` — zero-based page offset.

The response contains active incomplete Variants in oldest-created-first order together with
display names, identifiers, an optional primary-image ID, all current missing requirements and
pagination metadata. Intentionally inactive and archived Variants are excluded from employee work.
The queue is a computed read model: it joins current Catalog, primary-image and latest effective
retail-price facts in one bounded query. There is no readiness table or mutable `ready` flag.

When an employee adds the missing photo, price or identifier, the Variant disappears from the next
queue response automatically. The `requirement` and `search` filters change both the returned
items and `total`.
