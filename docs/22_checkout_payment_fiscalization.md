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

## Phase B: журнал, сверка и наблюдаемость

`FinancialOperationEvent` — append-only доказательная история значимых переходов оплаты,
фискализации, фоновых заданий и сверки. Текущее состояние по-прежнему определяется
`Sale`/`PaymentAttempt`/`Fiscalization` и job-моделями; журнал не является вторым state machine.
Событие записывается той же PostgreSQL-транзакцией, что и соответствующее изменение состояния.
Обычный ORM запрещает update/delete события. В журнал не попадают API keys, headers, данные карты
и необработанные ответы AQSI; сохраняются только безопасные ошибки, идентификаторы и ограниченные
metadata.

Перед финансовым POST job уже существует с устойчивым `correlation_id`. Worker отдельной
транзакцией переводит его в `PROCESSING`, сохраняет `dispatch_started_at` и событие
`*_DISPATCH_STARTED`, а затем начинает provider call. Если процесс завершился после marker, но до
сохранения provider operation ID, повторить POST безопасно невозможно: recovery переводит факт в
`UNKNOWN`/`MANUAL_REVIEW_REQUIRED`. Это консервативная at-most-one-automatic-dispatch граница, а
не обещание exactly-once у внешнего провайдера.

При наличии provider operation ID кнопка «Проверить статус» ставит существующий job в очередь
только для authoritative GET. Row lock и повторная проверка состояния объединяют одновременные
нажатия. Подтверждённая оплата проходит через обычный completion path: StockMovement создаётся
один раз, а FiscalizationJob — по существующим уникальным ограничениям. При отсутствии provider
ID API не создаёт новый charge/receipt, один раз фиксирует inconclusive/manual-review и показывает
correlation ID, сумму, время и настроенную identity кассы для ручного сопоставления. Поиск AQSI по
локальному correlation ID не используется: подтверждённого provider API для такого поиска нет.

Экран «Чеки и операции» показывает ограниченную timeline последних событий и позволяет копировать
correlation/provider IDs. Доступ проверяется через ownership Sale; чужая финансовая история не
выдаётся. История не удаляется после разрешения инцидента. Старые production-инциденты не
исправляются автоматически и отражаются в их сохранённом состоянии.

Health endpoint разделяет:

- доступность AQSI Cloud;
- heartbeat RQ worker очереди `checkout`;
- количество PostgreSQL jobs в `QUEUED` и `PROCESSING`;
- возраст самого старого ожидающего job и число зависших jobs;
- локальную операцию текущего пользователя и глобальное наличие фоновой работы.

Порог heartbeat, долгого `QUEUED` и stale `PROCESSING` задаётся через
`CORE_CHECKOUT_WORKER_STALE_SECONDS`, `CORE_CHECKOUT_QUEUED_JOB_STALE_SECONDS` и
`CORE_CHECKOUT_PROCESSING_JOB_STALE_SECONDS`. Баннер использует один устойчивый компонент и четыре
состояния `READY/BUSY/ATTENTION/OFFLINE`; polling не связан с перерисовкой Sale, параллельные
запросы объединяются, устаревшие ответы игнорируются, визуальная смена сглаживается на 650 мс.

### Ручной UAT Phase B

Все финансовые сценарии выполняются только оператором на месте и малыми суммами. Автотесты не
посылают команды в реальную AQSI.

1. Карта и QR: создать две малые Sale, выбрать «Карта / QR», проверить показ суммы/QR на кассе,
   завершение оплаты, единственный StockMovement, чек и timeline событий.
2. Наличные: отдельно проверить «Наличными + чек» и «Наличными без чека»; acquiring screen не
   должен открыться, а fiscal status должен соответственно завершиться или стать `SKIPPED`.
3. Во время `PAYMENT_PENDING`/`FISCALIZATION_PENDING` уйти в другой раздел и вернуться: обработка
   продолжается на сервере, Sale и события остаются доступными.
4. На тестовой незавершённой операции перезапустить worker: при известном provider ID должен
   продолжиться только status GET; без ID должна появиться ручная проверка без нового POST.
5. Для `UNKNOWN` с известным ID нажать «Проверить статус» дважды: новая оплата/чек и повторное
   списание остатков не создаются. Для `UNKNOWN` без ID проверить понятную блокировку и diagnostics.
6. Остановить worker и затем запустить обратно: баннер показывает проблему worker независимо от
   AQSI Cloud; после heartbeat состояние стабилизируется без мерцания.
7. Открыть завершённую и историческую Sale в «Чеки и операции»: проверить статусы, provider ID,
   correlation ID, безопасную ошибку и хронологию; чужой оператор не должен видеть Sale.

Для аварийного восстановления сначала сверяются acquiring operation, `slip.id`, сумма и список
чеков AQSI. Эквайринговый slip подтверждает оплату, но сам не является фискальным чеком. Если
оплата `Completed`, а чека по этому slip нет, выполняется только обычный `receipt.process` с
исходным slip и frozen SaleItem; повторный purchase запрещён. После неизвестного результата
`receipt.process` новая попытка допустима только после повторной однозначной сверки списка чеков.

## Phase C: аварийное «Оплачено на кассе»

Если исходная CARD/QR-операция остаётся `PENDING` или `UNKNOWN`, администратор — владелец Sale
может явно подтвердить, что оплата принята и фискальный чек уже сформирован непосредственно на
физической кассе. Действие не зависит от health AQSI, не вызывает acquiring/fiscal provider и
завершает исходную Sale через тот же exactly-once Inventory path, что и
`cash_without_receipt`.

CORE создаёт отдельный эффективный `PaymentAttempt(CASH, SUCCEEDED)` с безопасными metadata
`source=EXTERNAL_POS`, `confirmation=OPERATOR_CONFIRMED`, ссылкой на исходную CARD-попытку и
подтверждением риска. Исходная попытка, provider operation ID, correlation ID, ошибки и события
сохраняются. Для неё устанавливается только учётная связь `accounting_superseded`: это не меняет
фактический provider status и не означает отмену.

Один `Fiscalization(SKIPPED)` фиксирует `external_receipt_confirmed=true`. Здесь `SKIPPED`
означает «CORE не создаёт второй чек», а не «чек не требовался». FiscalizationJob не создаётся.
Если фискальная команда уже существует, аварийное действие отклоняется и требует отдельной
сверки.

Sale блокируется `FOR UPDATE`; повторный POST возвращает тот же результат. Если approval AQSI
зафиксирован первым, аварийное подтверждение отклоняется. Если первым завершился внешний POS,
поздний `APPROVED` сохраняется в исходном PaymentAttempt, помечается как неэффективный для
повторного revenue и создаёт `LATE_PROVIDER_APPROVAL_DETECTED` плюс ручной инцидент. Inventory,
Sale revenue и чек CORE повторно не создаются; автоматический refund не выполняется.

Worker всегда продолжает конкретный `payment_attempt_id` своего durable job, а не последнюю
попытку Sale. После fallback команда без provider operation ID блокируется; известный ID можно
только читать. Recovery не возвращает завершённую внешним POS Sale в `PAYMENT_PENDING`.

### Ручной UAT Phase C

1. Создать малую CARD/QR Sale и дождаться `PENDING`/`UNKNOWN`; убедиться, что обычный unsafe retry
   отсутствует, а администратору доступно «Оплачено на кассе».
2. Нажать действие, проверить текст риска, выбрать «Назад» — финансовые факты не меняются.
3. Повторить и подтвердить только после реальной оплаты и чека на кассе: AQSI payment/receipt
   screen не должен открываться, Sale становится `COMPLETED`, Inventory списан один раз.
4. В «Чеки и операции» проверить «Оплачено на кассе вручную», исходную AQSI-попытку,
   correlation/provider IDs и timeline событий.
5. При известном provider ID выполнить «Проверить статус»: отправляется только status GET. Если
   AQSI позднее вернёт `APPROVED`, UI показывает возможное двойное списание и рекомендацию
   расследовать возврат; автоматического refund нет.
6. Повторно нажать аварийное подтверждение и перезапустить worker: второй платёж, чек, Sale или
   StockMovement не появляются.
7. Проверить, что обычный оператор без admin/superuser получает `403`, а чужая Sale — `404`.

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

## Phase D: изоляция payment и fiscal providers

Sales использует только provider-neutral `PaymentProvider` и `FiscalProvider`. Статусы внешней
системы нормализуются адаптером в `PENDING`, `SUCCEEDED`, `FAILED`, `CANCELED` или `UNKNOWN`;
provider-specific status не участвует в переходах Sale. Ошибки дополнительно различают
неизвестный исход команды, окончательную ошибку, временную ошибку status query и неподдерживаемую
операцию. Raw response и credentials в Sales/UI не передаются.

`CheckoutProviderFactory` выбирает payment и fiscal capability отдельно. Для существующей AQSI
Integration оба назначения по умолчанию включены, поэтому старые настройки и historical rows
работают без миграции. Если роли требуется разделить между двумя Integrations, у первой задаётся
`payment_enabled=true, fiscalization_enabled=false`, у второй — обратная комбинация. Эти флаги
являются простыми assignments, а не module toggles: Sales и финансовая история всегда остаются
доступны.

Выбранная fiscal Integration записывается в безопасные metadata PaymentAttempt до фоновой
обработки оплаты. После подтверждения оплаты Fiscalization получает собственные `integration_id`
и `provider`. Payment/fiscal workers восстанавливают обе сохранённые ссылки и при известном
operation ID выполняют только status query. Durable intent, dispatch boundary, correlation ID,
UNKNOWN quarantine, event journal, exactly-once local Inventory и emergency fallback не менялись.

Persisted `FiscalizationJob.aqsi_operation_id` пока не переименован: это историческая колонка.
Generic worker обращается к ней через provider-neutral `provider_operation_id`, а API временно
возвращает оба имени для обратной совместимости. Cancellation, refund и отдельный receipt-details
workflow не реализованы; capability registry их не заявляет для AQSI.

### Ручной UAT Phase D

1. На текущей единственной AQSI Integration выполнить малую CARD/QR оплату и cash-with-receipt:
   поведение, чек, Inventory и журнал должны совпасть с Phase B/C.
2. Перезапустить checkout worker при известных payment/fiscal operation IDs: должны выполняться
   только status GET без второго purchase/receipt POST.
3. Проверить emergency «Оплачено на кассе»: исходная provider operation и timeline сохраняются,
   повторная внешняя команда не создаётся.
4. Отдельную физическую конфигурацию двух провайдеров не проверять до появления второго реального
   адаптера; её readiness покрывается fake provider contract tests.

## Phase E: operational reliability и кассовый UX

Health endpoint хранит сигналы раздельно: конфигурация и API-доступность PaymentProvider,
конфигурация и API-доступность FiscalProvider, подтверждаемая провайдером готовность физического
устройства, heartbeat worker, размеры очереди и stale jobs. Успешный read-only запрос AQSI Cloud
не считается доказательством готовности кассы: текущий AQSI adapter возвращает
`physical_device_ready=null`, а UI честно пишет, что готовность проверяется во время операции.
Health probe использует только безопасное чтение списка shops и не создаёт payment/receipt command.

Если payment и fiscal capabilities обслуживает один adapter, один health poll вызывает его ровно
один раз. При раздельных Integrations endpoint возвращает отдельные имена и результаты. In-flight
checkout и worker recovery по-прежнему используют сохранённые IDs, поэтому изменение текущей
конфигурации не перенаправляет историческую операцию.

Sales сохраняет один постоянный баннер `READY/BUSY/ATTENTION/OFFLINE`. До первого ответа он
показывает «Проверяем состояние», а не ложное «Касса готова». Компонент переживает Sale rerender,
объединяет параллельные запросы, игнорирует устаревшие ответы, опрашивается отдельно раз в 15 секунд
и применяет presentation-only coalescing 650 мс. `READY` означает готовность CORE, worker и provider
API, но не неподтверждённую физическую готовность устройства.

Основной checkout показывает только понятный итог: подтверждена ли оплата, определён ли результат,
что происходит с чеком и какое безопасное действие доступно. Provider operation ID, correlation ID,
job attempts и sanitized technical error сохранены в «Чеки и операции». Фильтр «Требуют внимания»
включает UNKNOWN, manual review, late approval/double-payment risk, неотмеченную failed
fiscalization и jobs, действительно превысившие configured stale threshold.

### Ручной UAT Phase E

1. Провести малую оплату картой и отдельно QR; проверить сумму на устройстве, завершение оплаты,
   единственный чек и историю событий.
2. Провести наличные с чеком: acquiring screen не открывается, receipt и Inventory завершаются
   независимо.
3. Во время оплаты и формирования чека перейти в Catalog и вернуться: worker продолжает работу,
   баннер не прыгает и Sale восстанавливает состояние.
4. При доступном AQSI Cloud убедиться, что UI не заявляет физическую готовность кассы, если API её
   не подтверждает. При выключенном/недоступном устройстве проверять только штатную безопасную
   ошибку; не создавать специально UNKNOWN payment или второй чек.
5. Перезапустить worker во время операции с уже сохранённым operation ID: должен выполняться только
   status GET, без повторного purchase/receipt POST.
6. В «Чеки и операции» проверить payment/fiscal timings, provider/correlation IDs, failed receipt,
   stale job и late-approval предупреждение. Технические IDs не должны появляться в основной кассе.
7. Для заранее подготовленного безопасного сценария Phase C проверить отображение «Оплачено на
   кассе» и сохранение исходной операции; не провоцировать неоднозначное реальное списание.
