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