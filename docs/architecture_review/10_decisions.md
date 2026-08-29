| Sprint | Review      | ADR              | Статус      |
|--------|-------------|------------------|-------------|
| 08     | ✅ Approved  | ADR-002, ADR-003 | Completed   |
| 09     | ⏳ In progress | —              | In progress |
| 10     | ⏳ In progress | —              | In progress |

## Sprint 9 — Accepted decisions

- Rental моделируется вокруг `RentalAsset`.
- Один `RentalAsset` представляет ровно одну физическую вещь и не содержит `quantity`.
- `RentalAsset` связан с `Variant`.
- При завершении Intake отдельные `RentalAsset` для арендуемого `Variant` создает
  `CompleteIntakeWorkflow`.
- `CompleteIntakeWorkflow` координирует Inventory и Rental и владеет общей транзакцией.
- Rental и Inventory не координируют друг друга напрямую; Inventory не зависит от Rental.
- Текущие поля `RentalAsset` изменяются только через доменные команды.
- История бизнес-операций `RentalAsset` не удаляется и не переписывается.
- Вывод из аренды, подготовка к продаже и списание не удаляют `RentalAsset` и накопленную историю.
- `RETIRED` является терминальным состоянием.
- Фактическая продажа, бронирование, договоры и залоги отложены.

### RentalAsset economics and payback

#### Decision

Экономика аренды и окупаемость рассчитываются на уровне конкретного `RentalAsset`.

Для каждого экземпляра система должна позволять определить:

- стоимость приобретения и подготовки;
- суммарный фактический доход от завершенных аренд;
- количество завершенных аренд;
- процент окупаемости;
- факт окупаемости;
- первую завершенную аренду после достижения окупаемости.

Окупаемость определяется по фактическому доходу, а не только по количеству аренд.
Экономические показатели являются доменными понятиями и производными значениями, а не
утвержденным набором полей базы данных.

#### Rationale

Один `Variant` может иметь несколько физических `RentalAsset` с разной стоимостью приобретения,
подготовки, историей использования и доходностью. Расчет на уровне экземпляра позволяет оценивать
экономику каждой физической вещи и затем строить агрегированную аналитику по `Variant` и `Product`.

#### Consequences

- стоимость приобретения должна быть привязана к `RentalAsset` или однозначно выводиться для него;
- расходы на подготовку должны учитываться отдельно от стоимости приобретения;
- каждая завершенная аренда должна фиксировать фактический доход конкретного `RentalAsset`;
- метрики окупаемости вычисляются из исходных данных и не редактируются вручную;
- агрегированная аналитика по `Variant` и `Product` строится поверх экономики отдельных
  `RentalAsset`;
- правила распределения скидок, доставки и других общих сумм заказа между несколькими
  экземплярами должны быть определены до реализации финансовой аналитики.

#### Deferred

Отложены до следующих этапов:

- ROI с учетом ремонтов и прочих расходов;
- стоимость и длительность простоя;
- коэффициент использования;
- рейтинг доходности экземпляров и товаров;
- прогноз срока окупаемости;
- правила распределения общих сумм заказа, скидок и доставки между несколькими `RentalAsset`.

## Sprint 10 — Accepted decisions

### Customer

- `Customer` является отдельным aggregate root bounded context Customers.
- `Customer` не является `User`.
- `RentalOrder` хранит ссылку на клиента и обязательные snapshots имени и телефона.
- Физическое удаление клиента не применяется.
- Для прекращения использования карточки рекомендуется жизненный цикл `ACTIVE` / `INACTIVE`.

### RentalOrder

- `RentalOrder` является aggregate root.
- `RentalOrderItem` является Entity внутри агрегата, а не отдельным aggregate root.
- Snapshots клиента, номера экземпляра и названия позиции обязательны.
- Частичный возврат поддерживается: позиции одного заказа могут завершаться независимо.

### RentalAsset economics

- Доход считается на уровне конкретного `RentalAsset` по завершенным позициям аренды.
- Фактический `revenue` — сумма `charged_amount` завершённых (`RETURNED`/`LOST`) позиций.
- Фактические `expenses` — только стоимость записей обслуживания; наблюдение повреждения само по
  себе расходом не является.
- `net_income` и агрегированное поле `profit` равны `revenue - expenses` и не вычитают стоимость
  приобретения. В операторском UI это называется «Операционный результат», а не бухгалтерская
  прибыль.
- Окупаемость достигается в первый момент, когда накопленный доход за вычетом обслуживания
  становится не меньше неизменяемой стоимости приобретения RentalAsset.
- `payback_percent` и другие показатели окупаемости являются производными и не редактируются
  вручную.
- Аналитика по `Variant` и `Product` строится поверх экономики отдельных `RentalAsset`.

### Rental workflow

- `RentalOrder` и `RentalAsset` имеют независимые state machine.
- Application workflow координирует оба агрегата и владеет общей транзакцией.
- Агрегаты не изменяют состояние друг друга напрямую.

### Operator audit

- Persistence-проекция `RentalOrder` сохраняет оператора выдачи в `issued_by_id`, не расширяя
  Aggregate инфраструктурным идентификатором сотрудника.
- Каждая завершённая позиция сохраняет собственного оператора возврата или LOST в
  `completed_by_id`; это поддерживает частичный возврат разными сотрудниками.
- Время завершения позиции хранится в существующем `returned_at` независимо от terminal outcome.
- Операторы являются ссылками на `User`, а не на `Customer`.

### Lost asset

- `RentalOrderItem` может завершиться состоянием `RETURNED`, `LOST` или `CANCELLED`.
- `LOST` завершает позицию, но дальнейшая компенсация, списание и иные последствия определяются
  отдельным процессом.
- LOST не вызывает `RentalAsset.accept_return()` и не переводит экземпляр в `AVAILABLE`.

### Overdue

- `OVERDUE` не является хранимым статусом.
- Просрочка вычисляется для выданной позиции по плановой дате возврата и текущему времени.

## Sprint 10F — Accepted decisions

### RentalAsset passport

- Паспорт RentalAsset является read-проекцией, а не новым Aggregate или хранимым статусом.
- Выдачи, возвраты и LOST выводятся из существующих неизменяемых RentalOrderItem и RentalOrder.
- Поступление выводится из создания RentalAsset в Complete Intake.
- Списание выводится из терминального состояния существующего RentalAsset.

### Maintenance and damage journals

- Обслуживание и повреждения являются отдельными append-only фактами Rental bounded context.
- Журналы не входят в RentalAsset Aggregate и не обходят его state machine.
- Тип обслуживания хранится строковым бизнес-кодом; начальный словарь расширяется без изменения
  схемы базы данных.
- Фиксация повреждения не изменяет и не дублирует workflow возврата.

### Condition photos

- Исходный файл и метаданные принадлежат Media.
- Rental владеет связью изображения с RentalAsset, моментом `before`/`after` и необязательной
  ссылкой на RentalOrderItem.
- Полноценная галерея и обязательность фотографий отложены; текущая модель хранит доказательства
  состояния без изменения Aggregate.
# Sprint 10G — Rental economics decisions

- Acquisition cost is copied once from the completed Intake item to the created RentalAsset
  persistence record. There is deliberately no update command.
- Revenue is the sum of actual `charged_amount` values for completed returned or lost order items.
- Damage is evidence, not an expense. Only a maintenance record with `cost` contributes expenses.
- Payback, utilization and efficiency flags are read-model calculations and are never persisted as
  RentalAsset or RentalOrder statuses.
- “High expenses” means expenses are at least 50% of revenue (or any expense before revenue);
  “long idle” means no issue for 90 days after at least one rental.

## Sprint 9.11 — Catalog Management Foundation

- Product and Variant edits reuse Catalog application services and authenticated API commands;
  the first-party UI does not mutate ORM records.
- Retail, base rental price and recommended deposit are append-only `Price` facts owned by
  Variant. They are not mutable Variant columns.
- `rental` and `rental_deposit` are current catalog proposals. Actual deal values remain snapshots
  in `RentalOrderItem.agreed_price` and `RentalOrder.deposit_amount`.
- Product and Variant photos continue to use universal Media `Image + ImageLink`. Selecting a new
  primary image is one Media service command that demotes the previous primary link.
- Labels and AQSI remain independent modules and rebuild output from current authoritative Core
  data. AQSI stock synchronization is deferred because the current adapter has no stock contract.
- `CatalogVariantBarcode` is the normalized globally unique collection of scanner identifiers.
  Existing `CatalogVariant.barcode` remains the immutable primary INTERNAL EAN for migration and
  backward compatibility; it is not a second independently editable identifier.
- Migration `0027` copies every legacy EAN to the collection as `INTERNAL` without rewriting it.
- Manufacturer barcodes are append-only assignments to a Variant. EAN-13, EAN-8 and UPC-A use
  check-digit validation; other printable ASCII input is treated as Code 128 data.
- Intake owns no barcode registry. Its application workflow carries an unknown manufacturer code
  into Catalog Variant creation inside the existing completion transaction.
- AQSI's one-barcode projection prefers a suitable numeric MANUFACTURER code and falls back to the
  internal EAN. Product labels continue to encode the internal EAN; RentalAsset identity remains
  the independent immutable `RENT-...` value.
- Camera and keyboard scanners are input adapters only. Native `BarcodeDetector` is preferred when
  it supports EAN-13, EAN-8, UPC-A and Code 128; otherwise the UI lazily loads the fixed
  `@zxing/browser` 0.2.1 MIT UMD bundle. Both write the same manufacturer-barcode field and invoke
  the same lookup function. Production camera access requires HTTPS.
