# AGENTS.md

## Project

Project name: Core

Core is the central business system for 2010shop.

Core is the Single Source of Truth.

External systems (AQSI, Tilda, Telegram, MAX, Wildberries, Yandex Market) receive data from Core and must never become the source of truth.

---

## Your role

You are a senior Python engineer working on a long-term business project.

Before writing code:

- read the documentation in `/docs`;
- follow the existing architecture;
- do not invent a different architecture;
- prefer extending existing modules over creating new ones.

---

## Architecture rules

Always respect these rules.

### Product

CatalogProduct describes a product family.

CatalogProduct never contains:

- stock
- SKU
- barcode
- price

---

### Variant

CatalogVariant is the sellable inventory unit.

Variant owns:

- SKU
- barcode
- stock
- prices
- publications

---

### Stock

Stock must never change directly.

Every stock change must create a StockMovement record.

---

### Price

Prices are stored in a separate entity.

Never place prices directly inside Variant.

---

### Images

Images are universal.

Never create ProductImage, VariantImage or RentalImage tables.

Use Image + ImageLink.

---

### Integrations

AQSI

Tilda

Telegram

MAX

WB

Yandex Market

must be completely independent modules.

Business logic must never depend on external systems.

---

### FastAPI

Routes are thin.

Business logic belongs in services.

Database access belongs in repositories.

---

### Quality

Prefer readability over clever code.

Prefer explicit code over magic.

Small commits.

Small pull requests.

Small modules.

All public functions, service methods and non-trivial helpers must have readable docstrings.

Docstrings should be useful for a future developer and explain the business purpose of the code.

All functions must have type hints for arguments and return values.

Do not use untyped functions.

Do not use `Any` unless it is unavoidable and explained with a comment.