# AQSI Product Publication

## Scope

Sprint 7 publishes sellable `CatalogVariant` cards from Core to AQSI.

This boundary includes:

- creating an AQSI good;
- updating an already published good;
- assigning its retail price and primary barcode;
- binding it to the configured AQSI shop at the current retail price;
- recording who requested publication and what happened;
- verifying the remote representation after AQSI accepts the request.

It deliberately excludes:

- AQSI stock balance synchronization;
- receipt and inventory document synchronization;
- sales and return import;
- automatic deletion of remote goods;
- rental operations;
- image upload in the first implementation.

Those are separate workflows. Publishing a catalog card must never change the Core inventory ledger.

## Authority

Core remains the source of truth. AQSI is a sales channel and a remote projection of Core data.

Edits made directly in AQSI can be detected by verification, but they are not imported back into the Core catalog. The operator resolves drift by publishing the authoritative Core representation again.

## Official API contract

The integration uses AQSI API V2 with:

```text
x-client-key: Application <API key>
```

Product creation and update use the account-wide Goods API. AQSI V2 accepts external-system identifiers, so Core owns the remote identifier instead of relying on a generated AQSI ID.

Runtime configuration for the first installation:

```text
CORE_AQSI_ENABLED=true
CORE_AQSI_API_KEY=<secret generated in AQSI>
CORE_AQSI_TAX_CODE=6
CORE_AQSI_SHOP_ID=<required only when several active shops exist>
```

The committed example configuration keeps AQSI disabled and contains no real key.

The AQSI goods schema currently requires:

- `id`;
- `group_id`;
- `type`;
- `name`;
- `tax`;
- `unit`;
- `subject`;
- `paymentMethodType`.

Creation and update return a queue acceptance timestamp. HTTP 200 therefore means **accepted by AQSI**, not yet **verified on the cash register catalog**.

References:

- <https://api.aqsi.ru/>
- <https://aqsi.ru/support/integraciya-po-vneshnemu-api/>
- <https://aqsi.ru/support/sozdanie-i-otpravka-zaprosov-cherez-postman/>

## Stable identity

AQSI good `id` is the string form of the Core `CatalogVariant.id`.

This gives every Variant one deterministic AQSI identity and makes retries recoverable. SKU, barcode and title are mutable business data; none of them is suitable as the integration identity.

The same external ID is used for create, update and verification:

```text
CatalogVariant.id == AQSI Goods.id
```

## Minimal ordinary-goods payload

The first implementation sends only required AQSI fields plus the Core data needed to find and sell the item:

```text
{
  "id": "<CatalogVariant UUID>",
  "group_id": "<Core-managed AQSI group ID>",
  "type": "simple",
  "name": "<Product and Variant display name>",
  "tax": 6,
  "unit": "Штука",
  "unitCode": 0,
  "subject": 1,
  "paymentMethodType": 4,
  "sku": "<Variant SKU>",
  "price": 100.00,
  "barcodes": ["<primary barcode>"]
}
```

Meaning of the fixed ordinary-goods values:

- `type = simple` — an ordinary non-composite good;
- `unit = Штука` and `unitCode = 0` — a piece/unit;
- `subject = 1` — goods;
- `paymentMethodType = 4` — full settlement.

For the first Core installation, the confirmed value is `tax = 6` — VAT exempt (`НДС не облагается`).

The VAT code remains an explicit business choice during initial configuration rather than a universal Core default. Another installation may use a different tax regime.

Optional AQSI fields such as production cost, margin, image, marking type, cooking data, slot information, custom properties, minimum price and return restrictions are omitted from the first payload.

AQSI stores goods account-wide and exposes a separate Goods Prices operation that associates a good with a shop. After the goods card is verified, Core calls `POST /v2/Goods/prices` for the selected shop and verifies that the Goods read response includes that shop.

When an AQSI account has exactly one active shop, Core may select it automatically. Accounts with multiple shops must configure `aqsi_shop_id`; Core never guesses between several shops.

## Field mapping

| AQSI field | Core source |
| --- | --- |
| `id` | `CatalogVariant.id` as a string |
| `group_id` | Core-managed default AQSI group; later a Category mapping |
| `type` | AQSI-module ordinary-goods constant `simple` |
| `name` | Product title plus meaningful Variant title, limited to 128 characters |
| `sku` | `CatalogVariant.sku`, limited to 64 characters |
| `price` | current positive `retail` RUB Price |
| `barcodes` | one-element array containing the primary Variant barcode |
| `tax` | administrator-configured AQSI VAT code; `6` for the first installation |
| `unit` | AQSI-module ordinary-goods constant `Штука` |
| `unitCode` | AQSI-module ordinary-goods constant `0` |
| `subject` | AQSI-module ordinary-goods constant `1` |
| `paymentMethodType` | AQSI-module ordinary-goods constant `4` |

AQSI-specific fiscal fields are not accepted from the publication request and are not embedded in generic catalog models merely to satisfy one channel.

## Fiscal profile

Tax and receipt attributes affect fiscal documents and must not be guessed by application code.

The first implementation uses explicitly configured `tax = 6` (VAT exempt) and AQSI-module defaults for an ordinary piece good sold with full settlement. Later, an AQSI-specific Category or Variant override may support marked goods, services or other exceptional cases.

These fields belong to the AQSI integration module:

- `AqsiSettings` — endpoint, enabled state and secret reference;
- `AqsiFiscalProfile` — versioned VAT and AQSI receipt defaults;
- `AqsiGoodsPayload` — the external request contract;
- AQSI category mappings and remote identifiers.

Generic `CatalogProduct`, `CatalogVariant`, `Price` and Inventory models do not gain AQSI-only columns.

Publication is blocked when Core cannot resolve all required AQSI fiscal fields. The API key must be stored as a secret and must never appear in API responses, audit payloads or logs.

### Runtime VAT changes

VAT is a live business setting, not a deployment environment constant.

An administrator must be able to change the AQSI VAT code without editing files, rebuilding containers or restarting Core. A change creates a new immutable `AqsiFiscalProfile` version with:

- VAT code;
- `effective_from` timestamp;
- actor ID;
- optional reason;
- creation timestamp.

The previous profile remains available for audit and historical explanation. Past publication attempts keep their original payload snapshots and are never rewritten with the new VAT code.

Core resolves the profile effective at publication time. A future `effective_from` allows an announced tax change to be configured before it takes effect.

When a new profile becomes effective, the canonical AQSI payload hash changes. Existing AQSI publications are therefore reported as `outdated` and can be republished in a controlled background batch. Core does not silently send mass updates merely because an administrator saved a setting.

The profile change and any subsequent bulk-republication command require administrator authorization and audit attribution.

## Category strategy

AQSI requires `group_id` for every good.

There are no existing AQSI goods or groups to reuse. During integration bootstrap, Core creates one deterministic default AQSI category named `Товары Core` using the same VAT and ordinary-goods fiscal defaults.

The first Variants are published into that group. This avoids making full category synchronization a prerequisite for publishing the first product.

The durable design adds a channel mapping from Core Category to AQSI category ID. Category IDs must not be stored directly on `CatalogVariant`.

Resolution order:

1. mapped AQSI category for the Product category;
2. Core-managed default `Товары Core` group;
3. block publication with `missing_aqsi_category`.

## Channel readiness

`ReadyForSale` remains channel-independent. Passing it means the Variant is commercially complete in Core.

AQSI publication adds channel-specific requirements:

- AQSI integration enabled;
- API credentials configured;
- AQSI category resolvable;
- exactly one AQSI shop resolvable;
- fiscal profile complete;
- mapped name within AQSI limits;
- SKU and barcode within AQSI limits;
- current positive retail RUB price.

A Variant can therefore be Ready for Sale while not yet ready for AQSI.

## Bootstrap and discovery

Operators should not have to copy opaque AQSI identifiers blindly.

The integration bootstrap should:

1. validate the configured API key without exposing it;
2. fetch available AQSI shops and goods categories;
3. let an administrator select the default shop and fallback group;
4. ask an administrator to choose the VAT code;
5. create or verify the deterministic default `Товары Core` category;
6. show the resolved minimal fiscal profile for explicit confirmation;
7. save non-secret integration settings and report readiness.

Core must not silently infer legally significant fiscal settings and treat them as confirmed.

## Publication lifecycle

The current channel projection uses these meanings:

- `pending` — Core accepted the user's request and queued work;
- `accepted` — AQSI accepted create/update into its processing queue;
- `published` — a later AQSI read verified the expected remote representation;
- `failed` — a definitive error occurred or verification exhausted retries;
- `outdated` — the current Core payload differs from the last verified payload;
- `disabled` — channel publication was intentionally disabled in Core.

`outdated` can be derived by comparing the current canonical payload hash with the last verified payload hash. Catalog and Pricing modules do not call AQSI directly.

An individual PublicationAttempt additionally uses `processing` while a worker owns it. The worker commits that claim before starting network I/O, so no database transaction remains open while AQSI responds.

## Asynchronous workflow

Publication is a job, not an external HTTP call held inside a database transaction.

```text
Employee requests publication
        -> Core validates Ready for Sale and AQSI requirements
        -> Core records actor and canonical payload snapshot
        -> Core queues a publication job
        -> worker creates or updates the AQSI good
        -> AQSI returns queue acceptance
        -> worker verifies the good through AQSI read API
        -> publication becomes published or failed
```

The authenticated command endpoint should return HTTP 202 with the publication state. Repeated requests for the same current payload must be idempotent and must not create concurrent duplicate jobs.

## Persistence boundary

Use two responsibilities:

### Publication

Current projection state for one Variant and one channel:

- channel;
- Variant ID;
- external ID;
- current state;
- last verified payload hash;
- last successful time;
- last error summary;
- timestamps.

There is one Publication per `(channel, variant_id)`.

### PublicationAttempt

Immutable operational history:

- Publication ID;
- actor ID that requested it;
- canonical payload snapshot with secrets excluded;
- payload hash;
- attempt number;
- create or update operation;
- state transitions and safe response metadata;
- error code and sanitized message;
- requested, accepted and completed timestamps.

Attempts are never rewritten to hide an earlier failure. This supports employee attribution, troubleshooting and future operational analytics.

## Failure and retry rules

- Network timeout is an ambiguous result, not proof that AQSI rejected the good.
- The worker reads the deterministic external ID before retrying an ambiguous create.
- Retry transient network and server errors with bounded exponential backoff.
- Do not retry validation, authentication or malformed-payload errors until configuration or data changes.
- Store sanitized errors; never store the API key or complete request headers.
- A failed AQSI publication never changes catalog readiness, prices, media or stock.

## Deactivation and deletion

Core soft deletion or deactivation does not automatically delete the AQSI good in the first implementation.

Remote deletion is a separate explicit command because it is destructive and may affect cash-register operation. The future command must be authenticated, attributed and recorded as its own attempt.

## First implementation slice

1. AQSI configuration, secret handling and connection check.
2. Configured initial VAT code `6` and deterministic default category.
3. Canonical payload mapper with contract tests.
4. Publication and immutable PublicationAttempt persistence.
5. AQSI HTTP client behind an adapter interface.
6. Authenticated publish command returning HTTP 202.
7. Worker job with create/update, verification and retry policy.
8. Read API for current status and attempt history.
9. Fake-client integration tests before using real credentials.
10. One controlled sandbox or live smoke test with a dedicated test Variant.

The runtime fiscal-profile editor, scheduled VAT changes and controlled bulk republishing remain a documented follow-up. They do not block the first product publication slice.
