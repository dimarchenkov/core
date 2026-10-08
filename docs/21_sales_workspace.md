# Sales Workspace & Global Cart

## Реализованная граница Sprint 5.1

Core хранит обычные продажи как собственный серверный домен. Sprint 5.1 реализовал состояния
`DRAFT` и `CANCELLED`; checkout, скидки, manual/open items, оплата, фискализация и Inventory
movement типа `SALE` добавлены в Epic 5.2 и описаны отдельно в
[`22_checkout_payment_fiscalization.md`](22_checkout_payment_fiscalization.md).

Оператор может иметь несколько одновременных `DRAFT Sale`. Переключение не создаёт отдельного
`ON_HOLD`: черновик, который перестал быть активным, остаётся тем же `DRAFT` и показывается как
отложенный только на уровне интерфейса.

## Владение и активный выбор

Каждая `Sale` принадлежит одному `owner_id` — аутентифицированному пользователю, создавшему
продажу. List/read/command API всегда фильтруют по этому владельцу; идентификатор чужой продажи
не позволяет прочитать или изменить корзину.

Указатель активной продажи не является бизнес-фактом и не хранится в `Sale`. Первый клиент
сохраняет его в browser `localStorage` под ключом, включающим `user.id`. Поэтому:

- все товары и суммы остаются серверными и переживают перезагрузку страницы;
- один оператор может продолжить выбранную продажу после перезапуска того же браузерного
  профиля;
- разные операторы и разные browser profiles не делят один глобальный active pointer;
- потеря локального указателя не теряет Sale: любой собственный `DRAFT` доступен в переключателе.

## Commercial snapshot и totals

При первом добавлении Variant `SaleItem` сохраняет:

- `variant_id`;
- Product title, Variant title и готовый display label;
- SKU и текущий canonical barcode;
- текущую `RETAIL` Price в RUB;
- quantity и line total.

Повторное добавление того же Variant увеличивает quantity существующей строки. Изменения
Catalog или последующая Price history не переписывают snapshot. Все денежные поля используют
`Decimal`/`Numeric(12, 2)`, а line/Cart totals пересчитывает application service. Цена, которой
нет, не превращается в `0 ₽`: команда завершается отказом до автоматического создания Sale.

## Concurrency

Все мутации существующей корзины блокируют строку `Sale` через `SELECT ... FOR UPDATE`. Команды
количества относительные (`+1`/`-1`), поэтому клиент не отправляет устаревшее абсолютное значение.
Дополнительный unique constraint `(sale_id, variant_id)` запрещает две строки одного Variant.
Количество строки всегда положительно; уменьшение с единицы удаляет строку. Total пересчитывается
в той же транзакции.

## Catalog и рабочее место продажи

Catalog остаётся единственным большим визуальным picker. В sale-applicable строке Variant есть
компактная кнопка `+`; без active Sale сервер сначала создаёт `DRAFT`, затем добавляет Variant и
клиент сохраняет новый active pointer. При активной продаже Catalog показывает спокойный context
banner и переход обратно.

Sales workspace отвечает за переключение черновиков, изменение количества, удаление строк,
переход в Catalog, откладывание и отмену. Отдельного каталога или checkout UI внутри него нет.
Отмена сохраняет Sale и SaleItems для просмотра, запрещает дальнейшие изменения и не удаляет
историю.

## Global scanner

Build-free клиент содержит общий `ScannerService`, принимающий HID keyboard events. Для
проводных и быстрых HID-сканеров сохранён строгий 35 ms timing profile с минимум четыре
printable ASCII символа. Для Bluetooth HID последовательность из восьми и более символов
допускает до 180 ms между отдельными символами при среднем интервале не более 100 ms.
`Enter` и `Tab` принимаются как scanner suffix. В Sales workspace также есть явное поле штрихкода:
оно позволяет Safari и мобильным HID-устройствам передать код без зависимости от timing detector.

Detector сбрасывает buffer и ничего не маршрутизирует, когда событие приходит из `input`,
`textarea`, `select`, `contenteditable`, открытого dialog, IME composition или с Ctrl/Alt/Meta.
Обычный медленный ввод с Enter поэтому не становится продажей.

Маршрутизация явная:

1. handler текущего локального workflow;
2. глобальный Sales handler;
3. Sales handler автоматически создаёт `DRAFT`, если active pointer отсутствует.

Intake регистрирует локальный handler при открытии своего workspace и снимает его при переходе на
другой route. Скан на Intake попадает в существующий lookup Variant и не достигает Sales.
Неизвестный Sales barcode показывает non-blocking сообщение и ничего не создаёт.

## Stock и Rental

`DRAFT` и `CANCELLED` не резервируют и не уменьшают Inventory, не создают `StockMovement` и не
кешируют остаток. Наличие положительного stock не является инвариантом добавления в корзину.
Catalog может показывать текущий ledger balance, но это только operator context.

Sale выбирает количественный `CatalogVariant`; конкретный `RentalAsset` не выбирается и не
потребляется. Существующее отображаемое sale quantity (`ledger balance` минус tracked units,
выделенные не для продажи) остаётся read model Catalog и не превращается в destructive rule
черновика.

## Provider boundary

Sales domain и application service не импортируют AQSI или другой provider. Sprint 5.1 не
вызывает payment/fiscal adapter. Реализованный следующим Sprint 5.2 checkout зависит от отдельных
`PaymentProvider`/`FiscalProvider` contracts: текущая реализация использует Cloud API, а будущая
может использовать local SDK/device transport без изменения `Sale` и `SaleItem`.

Sprint 5.1 не создаёт Inventory `SALE` movements. В Sprint 5.2 они создаются exactly once после
подтверждённой оплаты. Provider response не становится источником Catalog или Pricing.

## Реализовано следующим шагом: Epic 5.2

Payment/fiscal lifecycle, идемпотентность, unknown outcome, Inventory timing, отрицательные остатки
и recovery зафиксированы в [`22_checkout_payment_fiscalization.md`](22_checkout_payment_fiscalization.md).
