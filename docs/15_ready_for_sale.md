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
