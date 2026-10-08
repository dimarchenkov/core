# Epic 5.2 — Checkout, Discount, Payment & Fiscalization

## Граница реализации

Epic 5.2 проводит frozen Sale от редактируемой корзины через оплату наличными или картой/QR к itemized fiscal
receipt. В Sale разрешены Catalog и manual/open позиции, а оператор может применить одну
receipt-level процентную скидку. Возвраты, refund, Customer/Loyalty, promo codes и local
SDK не входят в этап. AQSI Cloud API остаётся текущим adapter; Sales зависит только от generic
`PaymentProvider` и `FiscalProvider`.

## Состояния

Основной успешный путь Sale:

```text
DRAFT -> PAYMENT_PENDING -> PAID -> FISCALIZATION_PENDING -> COMPLETED
```

`PaymentAttempt` хранит независимые `PENDING`, `UNKNOWN`, `SUCCEEDED`, `FAILED`, `CANCELED`.
Authoritative `CANCELED` или `FAILED` означает, что успешного списания нет: попытка остаётся в
истории, а Sale безопасно возвращается в редактируемый `DRAFT`. Новая попытка создаётся только
явной retry-командой. Повтор исходного checkout request не создаёт второй charge.

`UNKNOWN` означает возможное списание при потерянном ответе. Sale остаётся `PAYMENT_PENDING`,
редактирование и новая оплата запрещены, Core выполняет только status lookup/reconciliation.
`FISCALIZATION_FAILED` не меняет успешную оплату и допускает retry только чека.

## Discount snapshot

Sale всегда хранит authoritative Decimal-факты:

- `subtotal_amount` — сумма исходных `SaleItem.line_total`;
- `discount_type=PERCENT`;
- `discount_value` — `0.00..99.99`;
- `discount_amount` — округление HALF_UP до копейки;
- `total_amount = subtotal_amount - discount_amount`.

Скидка меняется только в `DRAFT`. Значения, обнуляющие итог или фискальную цену хотя бы одной
единицы, отклоняются: текущий AQSI checkout требует положительную сумму платежа и положительные
цены позиций. Поэтому 100% не поддерживается.

## Распределение скидки

Core переводит каждую исходную unit price в точное дробное число копеек после скидки. Для каждой
единицы берётся floor, затем остаток до `Sale.total_amount` раздаётся по largest remainder:

1. большая дробная часть получает копейку раньше;
2. при равенстве используется стабильный порядок SaleItem;
3. внутри SaleItem первые единицы получают копейку раньше.

Равные unit prices группируются в persisted `fiscal_allocations`; их сумма хранится как
`fiscal_line_total`. Поэтому сумма фискальных строк всегда равна Sale total, а reload и fiscal
retry используют тот же frozen allocation, не пересчитывая Catalog/Pricing.

## Catalog и manual SaleItem

`SaleItem.source` различает:

- `CATALOG`: обязательный `variant_id`, snapshots Product/Variant/SKU/barcode;
- `MANUAL`: `variant_id=NULL`, обязательные только snapshot name, positive price и quantity.

Оба вида участвуют в subtotal, скидке, payment amount и itemized fiscal receipt. Manual item
использует общие явно настроенные AQSI tax system/VAT/payment-object defaults. Barcode для него не
выдумывается и в provider payload не отправляется.

Manual SaleItem никогда не создаёт Inventory movement: у Core нет Variant/stock identity. Это
сохраняет возможность будущего workflow «Непривязанные продажи», но linking/Product creation в
Epic 5.2 не реализованы.

## PaymentAttempt, наличные и идемпотентность

PaymentAttempt хранит method, frozen total/currency, provider
operation ID, status, unique idempotency key, attempt number и санитизированную диагностику. PAN,
card data, API key, authorization headers и raw provider response не сохраняются.

Для `CARD` сохраняются Integration/provider и запускается generic `PaymentProvider`. Для `CASH`
оператор явно нажимает «Получено наличными»: Core атомарно создаёт один `SUCCEEDED` PaymentAttempt
с `provider=NULL`, `integration_id=NULL` и точной frozen Sale total. Acquiring при этом не
вызывается. Row lock, уникальные attempt number/idempotency key и replay существующего успешного
cash-факта защищают от двойного клика.

Первый DRAFT transition защищён row lock. Core коммитит `PAYMENT_PENDING` и PaymentAttempt до
Cloud call. Double click/tab/replay видит тот же pending attempt и не вызывает acquiring повторно.
После `FAILED/CANCELED` новая попытка имеет новый attempt number/idempotency key и создаётся только
explicit retry. AQSI v4 не принимает Core idempotency key, поэтому при потерянном operation ID
Core сохраняет `UNKNOWN` и запрещает автоматический resubmit.

## AQSI acquiring, карта/QR и terminal cancel

Core отправляет точную discounted сумму в копейках через `POST /v4/Slips/process/purchase`, затем
опрашивает `/v4/Operations/{id}`. `Completed` принимается только с валидным purchase Slip и точной
суммой. AQSI `Canceled` отображается в generic `CANCELED`; `Timeout/Error`, полученные как
authoritative operation status, — в `FAILED`; transport timeout/connect loss — в `UNKNOWN`.

AQSI `acquiring_mode` хранится в Integration и принимает только `card_only`, `sbp_with_card` или
`sbp_only`; default — `sbp_with_card`. Оператор видит только локализованные «Только карта»,
«Карта / QR» или «Только QR», а AQSI-значения остаются в adapter/configuration boundary.

После terminal cancel нет Inventory или fiscalization. UI сообщает, что деньги не списаны,
возвращает редактирование и предлагает явную новую попытку.

## Inventory и fiscalization

После подтверждённой оплаты Core сначала создаёт immutable `SALE StockMovement` exactly once для
Catalog items. Manual items пропускаются. Отрицательный остаток разрешён и показывается warning;
конкретный RentalAsset не выбирается.

Затем одна durable Fiscalization формирует frozen positions из `fiscal_allocations`, включая
manual items. Receipt total обязан совпасть с payment/Sale total до копейки. `Completed` переводит
Sale в `COMPLETED`. При definite receipt failure Sale остаётся оплаченной с
`FISCALIZATION_FAILED`; retry повторяет только fiscal obligation и не создаёт payment/Inventory.

Fiscalization не зависит от наличия acquiring provider в PaymentAttempt. Для cash Sale она
создаётся через настроенный FiscalProvider и передаёт AQSI отдельную оплату чека type `0` без
эквайрингового Slip; для card/QR используется type `1` с подтверждённым Slip. Наличные никогда не
означают «чек не требуется».

## Recovery, несколько продаж и безопасность

Все состояния, попытки, allocation и external IDs находятся в PostgreSQL. Reload продолжает
polling `PAYMENT_PENDING`, `UNKNOWN`, `PAID`, `FISCALIZATION_PENDING` и показывает
`FISCALIZATION_FAILED`. Frozen Sale не получает scanner/Catalog additions; active pointer
очищается, а следующий add создаёт/выбирает другой DRAFT. После `CANCELED/FAILED` исходная Sale
снова может стать active DRAFT.

Checkout читает только `Integration`/`IntegrationCredential`; legacy env secret fallback нет.
Логи и API содержат только Sale/attempt/external IDs, состояния и безопасные ошибки. Будущий
Cloud/local SDK/device bridge может реализовать те же provider ports без изменения Sales domain.
