# Current

## Current Epic

**Epic 9 — Catalog Management**

Управление реальными карточками товара через обычный UI Core без обращения к SQLAdmin.

## Current Sprint

**Sprint 9.12 — Catalog Operations**

**Stage: Active**

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
- Rental имеет существенную завершённую реализацию, но из-за почти отсутствующего фактического
  использования дальнейшие Rental UX/UAT отложены до появления реального спроса. Доменная работа
  остаётся действительной и из roadmap не удаляется.

### Validated baseline from Sprint 9.11

- Intake Draft deletion, HEIC/HEIF/MPO normalization, Product autosave and contextual Catalog
  Media are implemented.
- Canonical Product label 40×30 and remote-CUPS/Xprinter direct printing are physically accepted.
- Physical iPhone/Safari Camera/Gallery and authenticated Media UI checks remain targeted UAT
  debt; automated checks do not mark them complete.
- Full implementation/UAT evidence is preserved in
  [Sprint 9.11 history](../docs/releases/sprint-9.11.md).

## Goal

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
- `SoftDeleteMixin.restore()` существует как технический primitive, но в Catalog отсутствуют
  repository/service/route с проверками Product/Variant graph и внешних интеграций. Поэтому
  действие «Восстановить» намеренно не показано: прямой сброс `deleted_at` из UI не добавлялся;
- Sprint 9.12 остаётся активным; desktop и physical iPhone/Safari UAT archive visibility ещё
  предстоит выполнить.

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

Автоматическая синхронизация AQSI не входит в Sprint 9.12. Она выделена в следующий Sprint 7.13.

## Definition of Done

- оператор видит актуальную цену каждого Product/Variant без обязательного открытия карточки;
- отсутствие цены и текущие известные AQSI publication/out-of-date/failure states различимы и
  фильтруются без заявления о remote drift detection;
- archive/delete не уничтожает существующую бизнес-историю;
- Category и Supplier управляются из обычного интерфейса без SQLAdmin;
- Catalog показывает известный AQSI status, но не запускает скрытую автоматическую синхронизацию
  и не обещает reconciliation drift detection;
- существующие границы Catalog, Pricing, Inventory, Intake, Rental и AQSI не смешиваются;
- результаты real-world UAT Sprint 9.12 честно зафиксированы.
