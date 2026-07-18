# Product Labels

## First template

Core generates a single-page printer-independent PDF sized exactly 58 x 40 mm.

The PDF embeds Roboto with Cyrillic support, so Russian product names render consistently in Docker, browser preview and Xprinter output without relying on host fonts.

The first retail template contains:

1. Product name, up to two lines.
2. Variant title and compact attribute values.
3. Current retail price in RUB as the strongest visual element.
4. Scanner-readable primary barcode with human-readable digits.
5. SKU and `2010shop` footer.

Purchase price, supplier, stock balance and internal comments are deliberately excluded.

## Business rules

Label data is not accepted from the caller. Core resolves the Product, Variant, current retail price, SKU and barcode from its own source of truth.

A sale label can be generated only after Ready for Sale passes. This preserves Photo First and prevents incomplete or unpriced items from reaching the shelf.

Generated 13-digit internal codes use EAN-13. Compatible legacy numeric codes use Code 128 so preserved identifiers remain printable.

## API

```text
GET /api/labels/variants/{variant_id}/58x40.pdf
```

The authenticated endpoint returns `application/pdf` for browser preview or printing.

If the Variant is incomplete, the endpoint returns HTTP 409 with the same machine-readable missing requirements as Ready for Sale.

## Printing boundary

The supported Sprint 7 workflow uses standard PDF printing:

1. An authenticated user opens the generated label PDF.
2. The operating system or browser print dialog sends it to the configured printer.
3. Printing uses actual size (`100%`) without page scaling.

Core does not send raw printer commands and does not require a printer driver on the application server in this workflow. The generated PDF remains the stable, printer-independent boundary.

## Target printer

The planned printer is **XPrinter XP-365B**, connected by USB to a future local Core server.

Automatic or silent USB printing is deliberately deferred until that server exists. It will be implemented as a separate local print adapter rather than coupled to label generation. The expected future path is:

```text
Core print job -> local print adapter -> TSPL over USB -> XPrinter XP-365B
```

The adapter may rasterize the authoritative 58 x 40 mm label before sending it to the printer. This keeps the existing template, Cyrillic rendering and barcode layout independent of printer-resident fonts.

Standard PDF printing remains the fallback even after a direct adapter is introduced.

Before production use, print calibration must confirm:

- physical 58 x 40 mm page size;
- printable margins;
- barcode scanning from the actual thermal print;
- darkness, speed and gap calibration;
- no browser scaling (`100%` / actual size).

Physical calibration and direct USB printing do not block Ready for Sale or Sprint 7 completion.
