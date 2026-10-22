# Epic 5.2 — Checkout, Discount, Payment & Fiscalization

## Граница реализации

Epic 5.2 проводит frozen Sale от редактируемой корзины через card/QR или один из двух явно
выбранных cash-сценариев. В Sale разрешены Catalog и manual/open позиции, а оператор применяет
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

`UNKNOWN` означает возможное списание при потерянном ответе или HTTP 5xx после финансового POST.
Sale остаётся `PAYMENT_PENDING`,
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

PaymentAttempt также фиксирует `fiscalization_required`. Card/QR всегда требует чек: публичного
варианта card-without-receipt нет, а инвариант дополнительно защищён constraint базы. Cash
предлагает два явных варианта: «Наличными + чек» (`true`) и «Наличными без чека» (`false`). Это
решение относится только к конкретной продаже; глобального выключателя фискализации нет.

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

Перед первым и повторным `POST /v4/Receipts/process` для card/QR AQSI adapter выполняет
read-only поиск `/v4/Receipts` по точному `slip.id` подтверждённого эквайринга. Найденный чек
принимается только при совпадении device, frozen Sale total и полном наборе фискальных реквизитов;
после этого существующий receipt ID привязывается к Fiscalization без нового POST. Несколько
совпадений, несовпадающие факты или недоступный lookup переводят Fiscalization в `UNKNOWN` и
блокируют автоматический повтор: AQSI receipt API не принимает Core idempotency key, поэтому
без однозначной сверки повтор мог бы создать второй фискальный чек.

Fiscalization не зависит от наличия acquiring provider в PaymentAttempt. Для «Наличными + чек»
она создаётся через настроенный FiscalProvider и передаёт AQSI отдельную оплату чека type `0` без
эквайрингового Slip; для card/QR используется type `1` с подтверждённым Slip.

Для «Наличными без чека» Core не вызывает PaymentProvider или FiscalProvider. После точного
Inventory posting создаётся durable Fiscalization со статусом `SKIPPED`, без provider/Integration,
и Sale становится `COMPLETED`. `SKIPPED` является отдельным намеренным фактом, а не success,
failure или unknown; история показывает «Фискальный чек — Не формировался».

## Recovery, несколько продаж и безопасность

Все состояния, попытки, allocation и external IDs находятся в PostgreSQL. Карточный checkout
сначала одной транзакцией создаёт `PaymentAttempt` и уникальный `PaymentJob`; только server-side
worker вызывает acquiring purchase. Browser инициирует команду и затем читает Sale, но не вызывает
AQSI status API и не поддерживает жизненный цикл операции. Повторная команда возвращает уже
существующий PaymentAttempt. Timeout/потеря сети означает `UNKNOWN`, а не финансовый отказ;
повторная оплата запрещена до сверки исходного AQSI operation ID.

После подтверждённой
оплаты Core в той же транзакции создаёт `Fiscalization` и уникальный `FiscalizationJob`. RQ служит
только сигналом пробуждения: authoritative очередь, число попыток, operation ID, retry deadline и
последняя ошибка хранятся в PostgreSQL. Worker захватывает job через row lock. Recovery sweep
возвращает в очередь due jobs и зависшие `PROCESSING` только при наличии provider operation ID,
который позволяет продолжить безопасный status query. Зависшая внешняя команда без ID переводится
в `UNKNOWN`/manual review и больше не запускается автоматически.

`retryable` transport-ошибка означает лишь возможность повторить безопасный GET-запрос. Она не
является разрешением повторять финансовый POST. `DEFINITE_FAILURE` допустим только при
authoritative terminal status/отказе, который гарантирует отсутствие результата; `UNKNOWN`
сохраняет возможность внешнего успеха; `TEMPORARY_QUERY_FAILURE` сохраняет исходный operation ID
и повторяет только его чтение. Число автоматических циклов неизвестного status query ограничено,
после чего job остаётся видимым как требующий ручной сверки.

Reload не управляет фискализацией: браузер только читает сохранённые состояния
`PAYMENT_PENDING`, `UNKNOWN`, `PAID`, `FISCALIZATION_PENDING` и показывает
`FISCALIZATION_FAILED`. Закрытие Safari, потеря сети оператора или открытие другой продажи не
останавливают чек. Frozen Sale не получает scanner/Catalog additions; active pointer
очищается, а следующий add создаёт/выбирает другой DRAFT. После `CANCELED/FAILED` исходная Sale
снова может стать active DRAFT.

Экран «Чеки и операции» хранит и сопоставляет Sale, все PaymentAttempt, provider operation ID,
Fiscalization, FiscalizationJob и receipt ID. История берётся из CORE, поддерживает фильтры по
периоду/состоянию и не исчезает после завершения. «Проверить» только ставит существующую операцию
на server-side reconciliation; acquiring purchase повторно не запускается.

Индикатор кассы объединяет настройку Integration, доступность AQSI Cloud API и наличие
незавершённых операций. Успешный Cloud API check намеренно не называется физической готовностью
терминала: публичный сигнал этого не доказывает.

AQSI v4 документирует единый Operations Hook (`GET/POST /v4/Operations/Hooks`), но в доступной
схеме не описана проверяемая подпись входящего уведомления, а настройка hook является общей для
аккаунта. Поэтому CORE пока не меняет существующий hook и не доверяет публичному callback.
DB-backed polling/reconciliation остаётся authoritative механизмом; webhook можно добавить позже
как оптимизацию после согласования адреса и доказуемой аутентификации, но не как единственный путь.

Checkout читает только `Integration`/`IntegrationCredential`; legacy env secret fallback нет.
Логи и API содержат только Sale/attempt/external IDs, состояния и безопасные ошибки. Будущий
Cloud/local SDK/device bridge может реализовать те же provider ports без изменения Sales domain.

Для аварийного восстановления сначала сверяются acquiring operation, `slip.id`, сумма и список
чеков AQSI. Эквайринговый slip подтверждает оплату, но сам не является фискальным чеком. Если
оплата `Completed`, а чека по этому slip нет, выполняется только обычный `receipt.process` с
исходным slip и frozen SaleItem; повторный purchase запрещён. После неизвестного результата
`receipt.process` новая попытка допустима только после повторной однозначной сверки списка чеков.

## Производительность фискализации

Исследование реальных операций 18–19 октября 2026 года показало следующую последовательность:

```text
T0 acquiring Completed
  -> 0,5–2,0 с: CORE обнаруживает оплату и отправляет receipt.process
T1 receipt.process start
  -> HTTP 9–75 мс в измеренной сети
T2 operationId получен
  -> 5,2–6,5 с: AQSI device operation Pending/Processing/Finishing
T3 AQSI receipt operation Completed
  -> 0,1–0,8 с: следующий poll и commit CORE
T4 Sale COMPLETED
T5 браузер отрисовал результат
```

Главная измеренная задержка находится внутри cloud/device operation AQSI, а не в VPN, HTTP или
фиксированном backend sleep. Прямой интерфейс кассы и Cloud API нельзя считать одним протоколом:
публичный API создаёт отдельную device operation, где `Pending` означает очередь, `Processing` —
выполнение на кассе, `Finishing` — обработку результата backend AQSI, и только `Completed` является
успешным финалом.

После `receipt.process` adapter немедленно возвращает operation ID в orchestration, чтобы CORE
сохранил его до отдельного status GET. Затем server-side worker опрашивает уже сохранённую операцию
с bounded backoff и продолжает работу независимо от браузера. Frontend делает только GET состояния Sale.
Повторный GET уже завершённого acquiring на каждом fiscal poll не выполняется.
`ttlMillis=120000` сохранён: это срок операции в очереди AQSI, а не задержка перед проверкой.
После пяти секунд UI явно показывает длительность обработки, но не объявляет оплату ошибочной.

Безопасные timing-логи отмечают T0–T4 и HTTP duration через monotonic clock; браузер пишет T5 и
время от первого отображения fiscal pending через `performance.now()`. В логи не попадают API key,
PAN, card data или полный acquiring slip. `receipt.process` по-прежнему не считается успехом в T2:
Sale становится `COMPLETED` только после authoritative `Completed` в T3 и durable commit в T4.
