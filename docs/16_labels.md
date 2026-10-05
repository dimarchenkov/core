# Product Labels

## Canonical Product label

The current Product label is a single-page, printer-independent vector PDF sized exactly
**40 × 30 mm**.

Its EAN-13 geometry is physically calibrated:

- X-dimension: 0.300 mm;
- 95 bar modules: 28.5 mm;
- quiet zones: 9 modules on each side;
- total symbol width: 33.9 mm.

The PDF embeds Roboto with Cyrillic support. Open PDF, Intake direct print and Catalog direct
print use the same `VariantLabelRenderer`; CUPS submits the finished PDF and never recalculates
label content.

The canonical label contains:

1. Product name.
2. A meaningful Variant name/attributes when they add information.
3. Current retail price, or an explicit missing-price state.
4. The current EAN-13 operational barcode with human-readable digits.
5. The `2010shop` footer.

It does not print the technical default Variant name or SKU. Purchase price, Supplier, stock
balance and internal comments are also excluded.

## Business rules

Label data is not accepted from the caller. Core resolves Product, meaningful Variant details,
current retail price and the current barcode from authoritative data.

A saved Variant identity is sufficient to open a label before Complete Intake. Label generation
does not change Inventory, Pricing or AQSI state. A missing retail price is not rendered as zero.

The Product renderer requires a valid 13-digit EAN-13 current barcode. If an external current
barcode has another supported scanner format, the operator must replace it with an EAN-13 value
before printing this Product label; Core must not silently print a hidden internal fallback.

## API

```text
GET  /api/labels/variants/{variant_id}/40x30.pdf
POST /api/labels/variants/{variant_id}/40x30/print?quantity=<1..500>
```

The authenticated GET returns `application/pdf` for browser preview or system printing. The POST
submits the same rendered PDF to the configured direct-print adapter.

The older Product endpoint/profile `58x40` remains available for backward compatibility. It is
not the canonical Product label and must not be used as the default in current workflows.

## Printing boundary and acceptance

Supported paths:

```text
Core renderer -> 40×30 PDF -> browser/system print
Core renderer -> 40×30 PDF -> CupsPrintingAdapter -> remote CUPS -> Xprinter
```

The application container has `cups-client`, not a printer driver or USB passthrough. The remote
CUPS queue owns the operating-system/Xprinter driver. Printing is a side effect outside Intake,
Catalog, Pricing and Inventory transactions.

The remote-CUPS path is physically accepted on Xprinter XP-365B: one-copy and three-copy jobs
produced the requested number of 40×30 labels with correct size, orientation and feed. PDF/system
printing remains the fallback.

See [Direct CUPS printing](direct-cups-printing.md) for configuration and acceptance evidence.

## RentalAsset labels

RentalAsset/RENT labels are independent inventory labels using the immutable `RENT-...` value and
Code 128. Their supported 40×30 and 58×40 profiles are not changed by the canonical Product-label
decision above.

## Historical note

The original Ready-for-Sale Product template was 58×40 mm and was documented in release `v0.4.0`.
That fact remains valid release history. Sprint 9.11 replaced the current Product default with the
physically calibrated 40×30 template.
