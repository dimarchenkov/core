# Current

## Current Epic

**Epic 4 — Rental follow-up**

**Epic status: ✅ Operational milestone completed; active follow-up in progress**

Завершённый операционный milestone Rental не переоткрывается и не считается незавершённым.
Текущий demand-driven follow-up расширяет его тарифами для реальной эксплуатации Karcher Puzzi
10/1.

## Current Sprint

**Sprint 4.19 — Rental Asset Identification, Seals & Components**

**Stage: Implemented; physical UAT pending**

## Current goal and scope

Добавить необязательные серийные номера, исторические контрольные пломбы и упорядоченную
комплектность физических `RentalAsset`, замораживать их в item-level снимке фактической выдачи и
сохранять результат проверки при возврате без отдельного Inventory/SKU и без изменения
существующего lifecycle доступности.

Sprint 4.14–4.18 реализованы; оставшийся physical multi-item/iPhone UAT и двусторонняя печать
не отмечаются принятыми. Sprint 4.19 реализован и ожидает физическую проверку маркировки,
замены пломб, проверки комплектности, выдачи и возврата на реальном оборудовании.

Deposit UX correction отделяет вычисляемый `required_deposit_total` от подтверждаемого
`deposit_received`: новый draft хранит `NULL`, READY лишь предлагает сумму всех item snapshots,
явный ноль сохраняется отдельно, а return settlement не подставляет рекомендацию вместо денег.

## Preserved parallel delivery — Epic 9 Catalog Management

**Sprint 9.12 — Catalog Operations: implemented; remaining physical UAT is preserved below.**

## Explicit parallel delivery — Epic 5 Sales Foundation

**Sprint 5.2 — Checkout, Discount, Payment & Fiscalization: implemented, physical UAT pending.**

**Sprint 5.20 — Sales Analytics: Revenue Dashboard: implemented, real-data and device UAT
pending.**

**Phase C — Emergency Checkout Fallback: implemented, physical UAT pending.**

**Phase D — Payment & Fiscal Provider Isolation: implemented, physical UAT pending.**

**Phase E — Sales Operational Reliability & UX: implemented, physical UAT pending.**

This user-requested vertical slice does not replace the active Epic 9 Catalog roadmap. It adds
operator-owned persistent `DRAFT`/`CANCELLED` Sales, multiple resumable carts, a browser-scoped
active Sale, global shell indicator, Catalog add actions, a dedicated cart workspace and
context-aware HID scanner routing with Intake priority. Epic 5.2 adds durable cash/card/QR PaymentAttempt,
separate Fiscalization, unknown-outcome recovery, exact-once Inventory `SALE` movements after
confirmed payment, receipt-level percentage discount, manual/open fiscal lines without Inventory,
terminal cancellation recovery, AQSI card/QR acquiring, explicit cash receipt/skipped choice and checkout UI.
Phase D keeps the same financial state machines and durable jobs while selecting payment and
fiscalization capabilities independently. Existing AQSI Integrations remain compatible by default;
future split selection uses the boolean `payment_enabled` and `fiscalization_enabled` assignments.
Customer/Loyalty and refunds/returns remain outside this slice. Architecture
and UAT details are recorded in [`docs/21_sales_workspace.md`](../docs/21_sales_workspace.md) and
[`docs/22_checkout_payment_fiscalization.md`](../docs/22_checkout_payment_fiscalization.md).
Sprint 5.20 adds authenticated read-only Revenue analytics over completed Core Sales and closed
Rental orders: local-date periods, separate source KPI/counts, combined daily totals,
Product/Variant/RentalAsset rankings, weekday Sales metrics and payment-method breakdown. Rental
uses historical base plus finalized overdue, while deposit and damage/loss compensation are
excluded. Refund adjustment and defensible COGS/profit remain explicitly deferred; definitions
and UAT are recorded in [`docs/24_sales_analytics.md`](../docs/24_sales_analytics.md).
Catalog-backed и manual/free-sale SaleItem входят в totals; source breakdown и отдельный Free Sale
ranking сохраняют различие источников и не создают Catalog identity по совпадению имени.

### Current operational assessment

- Intake прошёл несколько серьёзных real-world UAT и операционно достаточно пригоден для
  текущей работы. Возможные улучшения остаются polish/optimization backlog; редизайн Intake не
  является текущим приоритетом.
- Catalog — самое слабое ежедневное рабочее место (субъективная оценка UAT: около **3/10**):
  цена не видна в списке, для неё приходится открывать карточку, archive/delete практически
  недоступны из обычного UI, а управление категориями и поставщиками становится необходимым.
- Публикация каталога Core → AQSI уже используется в production и приносит операционную пользу;
  текущий известный publication/out-of-date/failure status должен быть виден прямо в Catalog.
- Физически подтверждены два itemized payment flow: Pending Order и direct AQSI checkout.
  Direct checkout без навигации по меню кассы — предпочтительное направление будущего обычного
  Sales/POS, но временный spike ещё не является постоянным доменом Sales.
- AQSI integration зависит от AQSI cloud API и доступности Internet. Поведение при недоступности
  сети/cloud остаётся открытым архитектурным решением Epic 5.
- Rental имеет существенную завершённую реализацию. Реальный спрос появился, Karcher Puzzi 10/1
  входит в эксплуатацию, поэтому тарифный follow-up стал текущим delivery focus. Завершённая
  доменная работа остаётся действительной и из roadmap не удаляется.

### Validated baseline from Sprint 9.11

- Intake Draft deletion, HEIC/HEIF/MPO normalization, Product autosave and contextual Catalog
  Media are implemented.
- Canonical Product label 40×30 and remote-CUPS/Xprinter direct printing are physically accepted.
- Physical iPhone/Safari Camera/Gallery and authenticated Media UI checks remain targeted UAT
  debt; automated checks do not mark them complete.
- Full implementation/UAT evidence is preserved in
  [Sprint 9.11 history](../docs/releases/sprint-9.11.md).

## Preserved Sprint 9.12 goal

Сделать Catalog пригодным как ежедневное операционное рабочее место без лишних переходов в
карточки Product/Variant и без технических обходных путей.

## Scope

- показать текущую retail price в списке Catalog и meaningful price для Variant;
- явно показывать состояние «цена отсутствует»;
- добавить понятные Product/Variant archive/delete actions из обычного UI;
- использовать soft-delete/archive, когда существуют Inventory, Sale, Rental или иные
  защищённые бизнес-факты; Price и локальная AQSI bookkeeping входят в dependency-aware
  preflight и удаляются только вместе с disposable Catalog entity без operational history;
- добавить UI управления Category: список, создание, редактирование, activate/deactivate и
  archive/delete согласно допустимой доменной семантике;
- добавить такой же операционный UI управления Supplier;
- показывать в Catalog текущий известный AQSI publication status и доступную диагностику;
- добавить полезные фильтры: missing price, never published / locally out-of-date or failed,
  inactive/archived — в пределах уже принятых lifecycle conventions.

### Sprint 9.12.1 — Catalog Shell (accepted)

- Catalog получил общий операционный shell с режимами «Продажа», «Аренда» и «Все»;
- desktop использует боковую навигацию по иерархии Category и операционные фильтры, mobile —
  отдельные доступные drawer для Category и фильтров;
- поиск, Category с включением потомков, Supplier через историю проведённых Receipt, фильтры
  «Нет цены», «Нет фото», «AQSI: проблема / не опубликовано» и «Остаток ≤ 0» выполняются на
  сервере, а состояние сохраняется в URL hash-route;
- существующие rental-фильтры сохранены внутри режима «Аренда»;
- Product cards в этом slice намеренно не переработаны: цены, Variant rows, stock/status badges,
  AQSI badge и archive actions остаются следующими частями Sprint 9.12;
- desktop и mobile visual UAT shell пройден, shell принят как baseline дальнейших slices.

### Sprint 9.12.2 — Product Cards (implemented, UAT pending)

- list-card показывает все активные Variant: meaningful name, SKU, текущую retail price или
  явное «Цена не указана», а также текущий ledger-баланс Inventory, включая `0` и отрицательные
  значения; технические default-названия скрываются через общее правило Label domain;
- реальное распределение каталога составляет от одного до восьми активных Variant на Product,
  поэтому сворачивание не вводилось и business data не скрывается по умолчанию;
- Category отображается ограниченным читаемым путём (не более трёх сегментов), AQSI — по
  существующему локальному Publication status и сравнению requested/verified payload hash без
  заявления о подтверждённом remote drift;
- режим «Продажа» минимизирует rental noise, «Аренда» сохраняет доход, количество аренд и
  доступность, «Все» использует компактное сбалансированное представление;
- «Результат аренды» остаётся существующим Rental economics: завершённый rental revenue минус
  maintenance expenses; это не общая прибыль магазина и не Sales revenue;
- цена, баланс, Category, AQSI и Rental economics загружаются пакетно; количество SQL-запросов
  list projection не растёт с количеством Product, Variant или RentalAsset;
- Variant identity в card остаётся единственной, а коммерческие факты разделены на компактные
  строки «Продажа» и «Аренда»: AQSI относится только к продаже, rental row использует текущую
  `PriceType.RENTAL` и количество актуальных `RentalAsset` с purpose `RENTAL`;
- rental row видим при наличии хотя бы одного RentalAsset с purpose `RENTAL`; sale row видим,
  если нет занятых tracked units (`purpose != SALE`), либо ordinary sale quantity ненулевой,
  либо существует текущая retail price, либо сохранена AQSI Publication. Sale quantity использует
  уже существующую семантику detail projection: Inventory ledger balance минус количество
  tracked units с purpose не `SALE`, а не rental quantity;
- отдельного признака sale intent в текущей модели нет: выбранный read-model predicate сохраняет
  missing-price warning для обычного sale-only Variant, но подавляет retail/AQSI warnings у
  полностью распределённого в Rental Variant без иных retail-фактов;
- Rental economics footer отделяет применимость от числового нуля: в режиме «Продажа» он скрыт,
  в «Аренда» и «Все» показывается только для Product с актуальными RentalAsset. Реальный Rental
  Product с нулевым результатом продолжает честно показывать «Результат аренды: 0 ₽»; фиктивная
  Sales economics до появления постоянного Sales domain не вычисляется;
- archive/delete, dictionaries и автоматическая AQSI synchronization не входят в этот slice;
- desktop и iPhone/Safari visual UAT Product Cards ещё не выполнен.

### Sprint 9.12.3 — Catalog Actions (implemented, UAT pending)

- Category badge в list-card стал непосредственным действием: picker показывает текущую
  Category, разрешает выбирать только активные Category и обновляет Product через существующий
  `CatalogProductService`; состояние режима, поиска и фильтров Catalog сохраняется;
- Product и каждый Variant получили видимые компактные действия hard delete внутри открытой
  Product card: Product action находится под списком Variant, Variant action — внутри своего
  блока; list-card остаётся плотной и не повторяет destructive controls. Необратимая операция
  доступна только administrator/superuser и всегда начинается с серверного dependency preflight;
- hard-delete eligibility вычисляется повторно внутри транзакции перед удалением. Любой
  `StockMovement`, любой `RentalAsset`, завершённая/закрытая Intake или проведённый/отменённый
  Receipt блокируют физическое удаление; stock ledger, rental history и документы никогда не
  каскадируются и не корректируются ради удаления;
- Product deletion graph при отсутствии blockers: Product, все его Variant (включая ранее
  архивированные), их `CatalogVariantBarcode`, `Price`, Product/Variant `ImageLink`, связанные
  draft-only Intake/Receipt item, а также локальные `Publication` и `PublicationAttempt` AQSI;
- Variant deletion graph ограничен выбранным Variant и его barcode/price/media/draft/AQSI
  зависимостями; Product-level ImageLink, Product и sibling Variant остаются;
- `Image` metadata и физические файлы не удаляются автоматически: hard delete снимает только
  полиморфные links, поэтому изображение, используемое другой сущностью, остаётся доступным;
- локальная AQSI publication bookkeeping удаляется только у Catalog entity без защищённой
  бизнес-истории. Adapter пока не поддерживает remote delete/deactivate, поэтому preflight явно
  предупреждает, что локальное удаление не удаляет товар с кассы;
- Product без Variant допустим текущей моделью. Для последнего Variant confirmation позволяет
  удалить только Variant и оставить пустой Product либо перейти к отдельному Product preflight;
- при blocker confirmation предлагает существующий безопасный archive (soft delete) как
  отдельную fallback-операцию; archive не подменяет hard delete disposable test data;
- `ActivityEvent` сейчас относится только к Intake session/item и не имеет Catalog FK; отдельной
  Sale/SaleItem модели в текущей схеме нет. Эти сущности поэтому не входят в deletion graph;
- automated backend checks реализованы; desktop и physical iPhone/Safari UAT delete/category
  flows остаётся выполнить вручную на старых test records.

### Sprint 9.12.3a — Archive Visibility (implemented, UAT pending)

- URL-backed Catalog state получил независимый параметр `status=active|archived|all`; значение
  по умолчанию — `active`, оно не пропускает soft-deleted Product или Variant в обычный Catalog
  и независимо сочетается с режимами `sale|rental|all`;
- desktop показывает постоянную группу «Статус» в левой панели, mobile — те же три radio option
  в существующем Filters drawer; отдельной archive page и bulk actions нет;
- архив определяется только существующим `deleted_at`; `is_active=false` остаётся отдельным
  operational состоянием и не переименовывается в archive;
- `Архивные` показывает soft-deleted Product, а также активный Product, у которого есть
  soft-deleted Variant. У архивного Product показываются его активные Variant независимо от их
  собственного archive marker; у активного Product в этом режиме показываются только архивные
  Variant. `Все` объединяет обе archive-группы, сохраняя marker на каждой архивной сущности;
- Product list-card остаётся тем же компонентом: архивный Product получает заметный `АРХИВ`,
  приглушённый фон/изображение и read-only Category badge; архивный Variant помечается отдельно,
  чтобы его состояние не смешивалось с состоянием Product;
- channel relevance считается только по Variant, видимым для выбранного status: поэтому
  `Аренда + Архивные` не получает RentalAsset через скрытый активный/архивный Variant, а
  `Все + Все` включает все физически существующие Product обеих archive-групп;
- hard-deleted строки физически отсутствуют и не получают trash/recycle-bin semantics; hard
  delete, bulk actions и изменение базового дизайна Product card в этот slice не входят;
- безопасное восстановление поверх `SoftDeleteMixin.restore()` добавлено отдельным application
  workflow в Sprint 9.12.3b; UI не меняет `deleted_at` напрямую;
- Sprint 9.12 остаётся активным; desktop и physical iPhone/Safari UAT archive visibility ещё
  предстоит выполнить.

### Sprint 9.12.3b — Restore from Archive (implemented, UAT pending)

- архив Product остаётся только Product-level lifecycle state: текущий archive не каскадирует
  `deleted_at` в Variant. Поэтому restore возвращает только Product и сохраняет собственное
  active/archived состояние каждого дочернего Variant;
- Variant восстанавливается отдельно только под существующим, неархивным и operationally active
  Product. Если родитель архивирован или inactive, API возвращает понятный blocker и требует
  сначала восстановить Product; combined restore и bulk restore не добавлялись;
- Product restore внутри одной транзакции блокирует целевую строку, проверяет существующую
  активную Category и отсутствие другого active Product с тем же slug. Недоступная Category не
  заменяется автоматически; archived-card UI пока не позволяет переназначить её, поэтому такой
  Product остаётся в архиве до отдельного category-correction flow;
- Variant restore сохраняет системные SKU и barcode без регенерации. Перед снятием archive marker
  повторно проверяются active owner SKU, operational barcode и current barcode history; конфликт
  блокирует restore без изменения Variant;
- restore меняет только lifecycle marker и audit updater. Stock ledger, Price, Intake/Receipt,
  RentalAsset/history, TEST scope и ImageLink остаются прежними и не дублируются;
- AQSI `Publication`/attempt state сохраняется дословно; restore не запускает публикацию или
  resync и не утверждает, что remote AQSI synchronized;
- кнопки «Восстановить товар» и «Восстановить вариант» видимы внутри тех же archived cards в
  `status=archived|all`. Product использует спокойное подтверждение, Variant — immediate action с
  feedback; после успеха Catalog перечитывается с тем же URL-backed mode/status/search state;
- mobile использует те же действия с touch target не меньше 44 px и существующий bottom-sheet
  confirmation; desktop и physical iPhone/Safari UAT restore flows ещё предстоит выполнить.

### Sprint 9.12.4a — Category Management (implemented, UAT pending)

- обычный Catalog получил явную точку входа «Управление категориями» рядом с существующим
  деревом и быстрым созданием; SQLAdmin, технический slug и raw parent ids оператору не нужны;
- Category сохраняет существующую single-parent hierarchy. Создание и редактирование принимают
  только название и optional parent, а все picker показывают полный путь вида
  «Канцелярия › Ручки»; Product по-прежнему имеет ровно одну primary Category;
- backend под row lock проверяет существование и operational availability parent, запрещает self
  parent, перенос под любого descendant и уже повреждённые циклические ancestor chains;
- archive остаётся soft-delete и разрешён только пустой leaf Category. Реальные counts активных
  Product и non-archived child Category блокируют операцию; товары не переносятся, дети не
  перепривязываются, hard delete Category не добавлялся;
- archived management view отделён от обычного дерева/picker. Restore возвращает Category с тем
  же parent и блокируется, если прежний parent отсутствует, archived или inactive; silent move в
  root не выполняется;
- Product create/edit/reassignment pickers используют только active, non-archived Category и
  читаемые hierarchy paths. Rename/move сразу учитываются существующим server-side Catalog
  descendant filter;
- `sort_order` сохраняется как существующее системное поле и продолжает задавать стабильный
  sibling order с title tie-breaker. Ручное ordering и drag-and-drop отложены: текущей
  операционной потребности для усложнения UI нет;
- mobile использует тот же management bottom sheet, одно-колоночные строки и touch actions не
  меньше 44 px; physical iPhone/Safari UAT ещё предстоит выполнить;
- Supplier management остаётся отдельным следующим slice и этим изменением не считается
  завершённым.

### Catalog Product Detail Refinement (implemented, UAT pending)

- крупный Product-level Info block удалён: SKU остаётся Variant identity, количество Variant
  видно в списке, а нулевая Rental analytics больше не доминирует в обычной sale-only карточке;
- раскрываемое «Управление товаром» сохранено только для Product fields и существующего flow
  «Добавить вариант»; Variant-specific редактирование находится внутри соответствующей карточки;
- `CatalogVariant.attributes` остаётся Variant-owned JSON mapping без миграции, но технический
  JSON editor полностью заменён на «Характеристики» с парами «Название / Значение», отдельными
  add/edit/delete actions и optional builder при создании Variant;
- новые и изменённые характеристики нормализуют окружающие пробелы; пустые имя/строковое
  значение и совпадение имён после trim отклоняются backend. Существующие `bool`/`int` значения
  читаются без изменения, а введённое оператором новое значение сохраняется строкой;
- generic «Редактировать» удалено. Название Variant меняется отдельным действием рядом с
  названием, PATCH не включает attributes или SKU, а SKU остаётся read-only;
- полный Rental block показывается только Variant с актуальными RentalAsset. Variant без них
  сохраняет компактную точку входа «Выделить в аренду», а Product без RentalAsset не показывает
  пустой Product-level Rental section;
- Product и Variant сохраняют независимые primary `ImageLink`; Variant по-прежнему использует
  Product primary как fallback. Product теперь использует Variant primary только когда своего
  primary нет и найден ровно один однозначный Variant primary; файлы не копируются;
- Inventory adjustment показывает существующие причины по-русски и принимает новый фактический
  остаток, но отправляет вычисленную delta в прежний append-only ledger workflow. Для `other`
  комментарий обязателен; новые enum members не добавлялись;
- автоматические проверки реализованы; desktop и physical iPhone/Safari UAT этого refinement
  остаётся выполнить по приложенному сценарию.

### Administrative feature — Test Data Purge (implemented, UAT pending)

- TEST status хранится явно как `is_test` на `CatalogProduct` и на workflow aggregate roots
  `IntakeSession`/`Receipt`; Variant, Price, Barcode, Inventory, Media и AQSI bookkeeping
  наследуют scope только через проверяемый граф и не получают дублирующие client-controlled flags;
- исторический Product никогда не становится TEST автоматически. Administrator/superuser сначала
  открывает dependency preflight и отдельно подтверждает «Пометить как тестовые данные»; команда
  атомарно маркирует Product и все его эксклюзивные Intake/Receipt roots;
- пустой Intake автоматически принимает TEST scope при добавлении первого TEST Product. После
  этого TEST и production Product нельзя смешивать; при completion scope переносится в Receipt и
  в новый Product, материализованный внутри TEST Intake;
- purge graph включает Product, все Variant, operational Barcode, Price, Product/Variant
  ImageLink, IntakeSession/IntakeItem, Receipt/ReceiptItem, их receipt-attributed StockMovement,
  неиспользованные RentalAsset из этих Intake, Intake ActivityEvent и локальные AQSI
  Publication/Attempt. `Image` metadata/files, Supplier, Category и User сохраняются;
- purge блокируется при смешанном Intake/Receipt, движении другого Product в test Receipt,
  StockMovement вне входящего в граф Receipt, RentalAsset без test Intake origin, RentalOrderItem,
  maintenance/damage/condition-photo history или production marker на уже TEST-графе;
- обычный Product hard delete не ослаблен: completed Intake, posted/cancelled Receipt, Inventory и
  RentalAsset остаются blockers, а fallback по-прежнему archive;
- локальная AQSI bookkeeping может быть удалена вместе с TEST graph, но remote AQSI object не
  удаляется: adapter не имеет безопасной операции remote delete/deactivate. Постоянной Sale/
  fiscal модели в Core пока нет; любое существующее `SourceType.SALE` inventory evidence блокирует
  purge как движение вне TEST Receipt;
- сервер блокирует classification/purge для обычного пользователя, требует `confirm=true`,
  повторно строит и проверяет graph под lock непосредственно перед мутацией и выполняет purge в
  одной транзакции. Успешно удалённый TEST Product физически отсутствует в Active/Archive/All;
- ручной desktop/mobile UAT административных confirmations ещё предстоит выполнить.

### Requested follow-up — Sprint 7.13b AQSI Catalog Projection (implemented, UAT pending)

- administrator включает автоматическую проекцию отдельно от общего enabled state интеграции;
- worker регистрирует один durable RQ sweep раз в пять минут и переиспользует существующие
  Publication/PublicationAttempt, canonical payload hash и bounded retry pipeline;
- sweep обрабатывает только operational Product/Variant, пропускает неготовые позиции и ставит в
  очередь только новые или локально изменившиеся projection;
- неизменившаяся failed projection не образует бесконечный periodic retry; после исправления
  доступна явная команда «Синхронизировать сейчас»;
- архивирование/удаление в AQSI, remote drift reconciliation и near-immediate domain events не
  заявляются реализованными без подтверждённой provider semantics;
- automated checks реализованы; controlled live AQSI UAT и наблюдение одного periodic sweep ещё
  предстоят.

### Catalog Collections (implemented, UAT pending)

- Category остаётся Product taxonomy, а новая Collection группирует concrete Variant для
  merchandising; Product/Variant ownership и Publishing не переработаны;
- STATIC Collection хранит уникальные explicit memberships с add/remove через поиск Variant;
  удаление связи не изменяет Variant или Product;
- DYNAMIC Collection хранит только validated typed rules PRICE/CATEGORY/STOCK/IS_ACTIVE,
  объединяет их через AND и вычисляет состав live по current retail Price, Product Category и
  immutable Inventory ledger; materialized membership, OR, nested rules и SQL fragments нет;
- тип STATIC/DYNAMIC неизменяем после создания; уникальный slug генерируется из имени и скрыт
  от оператора; inactive Collection скрыта из Catalog sidebar, но остаётся в management UI;
- `get_collection_variants` образует чистую boundary для будущего storefront/Publishing, однако
  существующая AQSI/Tilda projection этим slice не меняется;
- Catalog sidebar получил «Подборки»: активная STATIC/DYNAMIC Collection фильтрует обычные
  Product cards до совпавших Variant, сохраняя фото, AQSI, остаток и добавление в Sale;
  «Управление подборками» открывает STATIC member search/add/remove, DYNAMIC rule builder с
  count preview, enable/disable и безопасное удаление только Collection-owned links; Variant
  card сохраняет checkboxes/read-only automatic matches;
- automated backend/UI checks реализованы; physical desktop и iPhone/Safari UAT ещё предстоит
  выполнить. Подробности: [`docs/23_catalog_collections.md`](../docs/23_catalog_collections.md).

### Manual Variant Ordering (implemented, UAT pending)

- `CatalogVariant.sort_order` хранит nullable integer только в scope родительского Product;
  существующие `NULL`-значения сохраняют стабильный алфавитный fallback по title/id;
- Product detail показывает текущий effective order и даёт touch-friendly действия «Выше» /
  «Ниже» без ручного ввода чисел; сохранение нормализует полный sibling order как `10, 20, 30…`;
- новый Variant получает следующую позицию только если Product уже использует ручной порядок;
  удаление Variant не уплотняет оставшиеся значения, поэтому gaps допустимы;
- Catalog cards, Product detail, Variant search и DYNAMIC Collection results используют один
  Product-local order. STATIC Collection сохраняет собственный membership order;
- automated backend/UI checks реализованы; physical desktop и iPhone/Safari UAT остаётся выполнить.

## Definition of Done

- оператор видит актуальную цену каждого Product/Variant без обязательного открытия карточки;
- отсутствие цены и текущие известные AQSI publication/out-of-date/failure states различимы и
  фильтруются без заявления о remote drift detection;
- archive/delete не уничтожает существующую бизнес-историю;
- Category и Supplier управляются из обычного интерфейса без SQLAdmin;
- Catalog показывает известный AQSI status; автоматическая синхронизация запускается только после
  явного включения администратором и не обещает remote reconciliation drift detection;
- существующие границы Catalog, Pricing, Inventory, Intake, Rental и AQSI не смешиваются;
- результаты real-world UAT Sprint 9.12 честно зафиксированы.
# Pre-1.0 critical: Payment & Fiscalization Reliability

Надёжность реальной оплаты и фискализации имеет приоритет над менее критичными pre-1.0
возможностями. Browser является только инициатором/наблюдателем; acquiring и fiscalization
продолжаются через durable DB jobs, worker recovery и безопасную reconciliation.

P1 incident resolution добавляет штатную операторскую сверку late approval и независимую проверку
фискального документа. Исходные payment/fiscal facts не переписываются, новые внешние команды не
создаются; до применения migration `0055_incident_resolutions` функциональность остаётся
в UAT pending.

Emergency workflow simplification добавляет только device-command cancellation после status GET:
это не reversal/refund и не доказательство отмены списания. Один compact resolution UI сохраняет
операторское evidence в существующих incident/event моделях, а payment/fiscal tracks остаются
раздельными. Frozen SaleItems показываются до технической истории. До применения migration
`0056_emergency_choices` новые варианты решения остаются в UAT pending.
