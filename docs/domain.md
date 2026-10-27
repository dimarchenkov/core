# Core Domain Language

## Назначение

Этот документ фиксирует общий язык Core. Термины описывают предметную область, а не таблицы, ORM-модели или экраны интерфейса.

## PhysicalObject

Обобщающее понятие реального объекта, жизненным циклом которого управляет Core.

В текущей модели PhysicalObject не обязан быть отдельной таблицей или базовым классом. Термин объединяет продаваемые складские позиции и конкретные арендные экземпляры на уровне языка продукта.

## Product

Семейство или общее описание товара.

Product отвечает на вопрос: «Что это?»

Product не является продаваемой складской единицей и не хранит SKU, штрихкод, цену или остаток.

## Variant

Конкретная продаваемая складская позиция Product: цвет, размер, комплектация или другая комбинация характеристик.

Variant отвечает на вопрос: «Что именно продаётся и учитывается количеством?»

Variant владеет SKU. Цены, изображения, движения и публикации остаются отдельными понятиями.

Порядок Variant задаётся только внутри родительского Product. Опциональный ручной порядок имеет
приоритет; Product без ручного порядка сохраняет детерминированный алфавитный порядок. Это
представление не является глобальным порядком всех Variant и не кодируется в name или SKU.

## Collection

Мерчандайзинговая группа конкретных Variant, независимая от каталожной таксономии.

Category отвечает на вопрос «что это за Product?», а Collection — «где и как показать этот
Variant?». Поэтому Category остаётся у Product, а Collection всегда работает с Variant и не
добавляет `category_id` в Variant.

STATIC Collection хранит явно выбранные оператором Variant. DYNAMIC Collection хранит только
типизированные правила, объединённые через AND, и вычисляет состав по текущим Price, Product
Category, Inventory ledger balance и operational activity. Вычисленный состав не
материализуется и не становится вторым источником истины.

В ежедневной работе активная Collection — primary filter обычного Catalog, а не отдельный тип
карточки. Поэтому «Всё за 50», «До 100 ₽», «На кассе» и «Акция» обычно являются Collections;
Category продолжает описывать, чем Product является (например, «Игрушки → Сквиши»).

## RentalAsset

Конкретный физический экземпляр для аренды.

RentalAsset отвечает на вопрос: «Какой именно экземпляр выдан клиенту?»

RentalAsset имеет собственный код, состояние, комплектность, пломбы, осмотры, обслуживание и историю выдач. Он не заменяет Product или Variant.

## Receipt

Документ поставки от одного Supplier.

Receipt отвечает на вопрос: «Что поступило, от кого, когда, в каком количестве и по какой закупочной цене?»

Draft Receipt не изменяет склад. Posted Receipt создаёт движения. Cancelled Receipt сохраняет исходные движения и добавляет компенсирующие.

Receipt не создаёт каталожные сущности. Если нужного Variant нет, сотрудник проходит workflow регистрации товара и затем добавляет Variant в Receipt.

## Movement

Неизменяемое событие, которое изменяет количество Variant в inventory ledger.

Movement является источником истины для остатка. Остаток вычисляется как сумма `quantity_delta`; прямое изменение поля stock запрещено.

Ошибки исправляются компенсирующими движениями, а не изменением или удалением истории.

## Sale

Транзакционный контекст продажи. Несколько operator-owned `DRAFT` могут существовать
одновременно; Active Sale является browser-workspace selection, а не server-global состоянием.
Checkout замораживает Cart и проводит Sale через `PAYMENT_PENDING`, `PAID`,
`FISCALIZATION_PENDING` к `COMPLETED`; определённые ошибки сохраняются как `PAYMENT_FAILED` или
`FISCALIZATION_FAILED`. `CANCELLED` доступен только из DRAFT.

Sale хранит receipt-level процентную скидку отдельными snapshot-фактами: subtotal, процент,
discount amount и payable total. Definitive payment `FAILED/CANCELED` остаётся в истории попыток и
возвращает Sale в редактируемый DRAFT; `UNKNOWN` сохраняет блокировку.

Подтверждённая оплата создаёт отрицательные immutable Movement типа `SALE` exactly once до
фискализации. Ошибка чека не отменяет оплату и складской факт. DRAFT/CANCELLED Inventory не меняют.

## PaymentAttempt

Отдельный факт оплаты: frozen amount, метод, optional Integration/provider operation ID,
idempotency identity и результат. `CARD` использует acquiring provider; подтверждённый оператором
`CASH` сразу хранится как `SUCCEEDED` с provider/Integration `NULL`. `UNKNOWN` не равен `FAILED` и
запрещает слепую новую оплату. `fiscalization_required` фиксирует выбор конкретной продажи;
для CARD он всегда `true`.

Для асинхронного checkout PaymentAttempt также фиксирует выбранную FiscalProvider Integration в
provider-neutral metadata. Это позволяет PaymentProvider и FiscalProvider быть разными, не меняя
frozen сумму, payment state machine или исторические строки, где обе роли выполняла одна касса.

Аварийное «Оплачено на кассе» создаёт отдельный эффективный `CASH`-факт с источником
`EXTERNAL_POS` и подтверждением оператора. Исходная CARD-попытка не удаляется и не меняет свой
неизвестный результат только из-за ручного подтверждения; её поздний provider outcome остаётся
предметом сверки. Для учёта Sale эффективен ровно один PaymentAttempt.

## Fiscalization

Отдельный факт обработки чека по SaleItem snapshots. Required-варианты используют собственную
Integration/FiscalProvider; явный cash-without-receipt создаёт `SKIPPED` без внешнего вызова.
PaymentProvider и FiscalProvider выбираются независимо; Fiscalization хранит собственную
Integration и не выводит её из provider оплаты после создания.
Состояние и retry не изменяют успешный PaymentAttempt и не создают повторные Inventory movements.
При аварийном внешнем платеже `SKIPPED` означает, что CORE намеренно не создавал второй чек,
поскольку оператор подтвердил уже сформированный чек на физической кассе; это не равнозначно
обычной продаже «без чека».

## SaleItem

Историческая позиция Sale. `CATALOG` snapshot хранит Variant и Product/Variant label, SKU, barcode,
unit price и quantity. `MANUAL` snapshot не имеет Variant и требует только name, price и quantity.
Оба вида получают frozen fiscal allocation после receipt discount. Изменение будущего Catalog или
Price не переписывает SaleItem; Manual item участвует в чеке, но не в Inventory.

## Cart

Редактируемая часть DRAFT Sale в отдельном POS workspace. Текущий operational barcode разрешает
Variant и добавляет одну единицу; повторный scan увеличивает количество. Неизвестный barcode не
создаёт Catalog entities. Отложенная корзина остаётся DRAFT без отдельного `ON_HOLD` state.

## Customer

Общая бизнес-сущность с актуальными именем и контактами, используемая Rental, будущими Sales,
Loyalty, purchase history и customer service. Customer не является полями внутри Sale; документы
хранят ссылку и необходимые исторические snapshots.

## Rental

Жизненный цикл временной передачи конкретного RentalAsset клиенту.

Rental включает выдачу, срок, цену, залог, обязательные фото состояния, возврат, осмотр и возможное обслуживание. Rental является равноправным бизнес-процессом наряду с Sale.

## Workflow

Понятный путь человека к бизнес-результату, координирующий существующие доменные возможности.

Примеры:

- Receive Goods;
- Ready for Sale;
- Sell;
- Rental Checkout;
- Rental Return;
- Inventory Count.

Workflow не означает обязательный универсальный workflow engine или BPMN. На ранних этапах это application service, API и пользовательский путь с ясными шагами и инвариантами.

## Границы ответственности

```text
Photo / Media  → подтверждает и помогает идентифицировать объект
Catalog        → описывает Product и Variant
Collection     → группирует Variant для мерчандайзинга независимо от Category
Supplier       → отвечает, от кого поступил товар
Receipt        → фиксирует поставку
Movement       → фиксирует изменение количества
Customer       → хранит общую актуальную identity клиента
Sale           → владеет Cart, transaction, payment/fiscal state и фиксирует продажу
RentalAsset    → идентифицирует арендный экземпляр
Rental         → управляет временной выдачей RentalAsset
Workflow       → проводит человека через несколько доменных возможностей
```

Карточки сущностей предназначены для справки, состояния и истории. Ежедневная работа выполняется через workflows.
