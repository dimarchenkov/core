# Domain Rules

## CatalogProduct

`CatalogProduct` is a product family. It describes shared catalog information
and is not a sellable inventory unit.

`CatalogProduct` does not contain:

- SKU;
- barcode;
- price;
- stock;
- images.

## CatalogVariant

`CatalogVariant` is the sellable inventory unit. A variant owns its SKU.

## Separate concepts

Price, Stock, Image, and Publication are separate concepts. They must not be
stored directly on `CatalogProduct`.

Variant readiness validation will be implemented later.

## Image concepts

- Image source files are immutable.
- Retouched files are stored as master versions.
- Web and thumbnail versions are derived from the current master.
- Users find images through CatalogProduct, CatalogVariant, SKU or barcode.
- Core provides direct download of source and master files.
- Replacing a master image must not destroy the source file.

## Suppliers

- Supplier is a reusable counterparty reference, not a container of products.
- A Supplier may represent a company, sole proprietor, marketplace, individual, or internal production source.
- Suppliers are reused across many Receipts.
- Supplier does not change stock directly.
- Products supplied by a Supplier are derived from Receipt and ReceiptItem history.
- One Receipt may contain existing variants and newly created catalog items.
- A direct Supplier-to-Variant association is not part of the MVP.
- SupplierProduct may be introduced later for supplier SKU, source URL, minimum order quantity, and supplier-specific naming.