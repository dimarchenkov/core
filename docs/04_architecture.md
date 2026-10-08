# Core Architecture

## Назначение документа

Документ описывает техническую архитектуру Core: стек технологий, структуру проекта, основные слои приложения и правила взаимодействия модулей.

Core должен быть простым для запуска, понятным для развития и готовым к будущим интеграциям.

---

## Основной стек

### Язык

- Python 3.13

### Управление проектом

- uv

### Backend

- FastAPI

### Database

- PostgreSQL

### ORM

- SQLAlchemy 2

### Database migrations

- Alembic

### Admin

- SQLAdmin

### Background jobs

- Redis
- RQ

### Images

- Pillow

### Barcode

- python-barcode
- qrcode

### PDF

- ReportLab

### Deployment

- Docker Compose

### Reverse proxy

- Angie

### Storage

- Local filesystem (MVP)

### Frontend

- SQLAdmin (MVP)
- React (Future)

---

## Главный принцип

Core является backend-first системой.

Core также является API-first системой: бизнес-возможность считается полноценной, когда она доступна через документированный API. Конкретный frontend является заменяемым клиентом API.

Сначала создается надежная серверная часть:

- база данных;
- бизнес-логика;
- API;
- админка;
- обработка фото;
- интеграции.

Красивый frontend можно добавить позже без изменения ядра.

---

## Высокоуровневая схема

```text
User / Phone / Browser
        |
        v
FastAPI Application
        |
        +-- Catalog
        +-- Inventory
        +-- Pricing
        +-- Media
        +-- Customers
        +-- Sales
        +-- Rental
        +-- Publishing
        +-- ImportExport
        +-- Users
        +-- Audit
        |
        v
PostgreSQL
        |
        +-- Local file storage
        +-- Redis / RQ
        |
        +-- AQSI
        +-- Tilda
        +-- Telegram
        +-- MAX
        +-- WB
        +-- Yandex Market
```

---

## Слои приложения

### API Layer

Отвечает за HTTP endpoints.

Пример:

```text
POST /api/products
GET /api/products
POST /api/intake/sessions
POST /api/publications/aqsi
```

API не должен содержать сложную бизнес-логику.

---

### Service Layer

Основная бизнес-логика.

Примеры:

- создать товар;
- создать вариант;
- принять товар на склад;
- сгенерировать SKU;
- загрузить фото;
- отправить товар в AQSI;
- провести продажу;
- создать договор аренды.

Sales следует тем же слоям: `sales/routes.py` переводит HTTP-команды, `SaleService` владеет
правилами корзины и транзакциями, а `SaleRepository` — выборками, ownership filters и row locks.
Первый web client хранит только scoped active-Sale pointer; Sale, SaleItem и totals всегда
серверные. `CheckoutService` обращается только к `PaymentProvider`/`FiscalProvider`, фиксирует
durable checkpoints до внешних вызовов и не импортирует AQSI. Cloud API или будущий local SDK
остаются деталями integration adapters.

---

### Repository Layer

Работа с базой данных.

Сервисы не должны напрямую писать SQL вразнобой.

---

### Integration Layer

Работа с внешними системами.

Каждая интеграция должна быть изолирована:

```text
integrations/
├── aqsi/
├── tilda/
├── telegram/
├── max/
├── wb/
└── yamarket/
```

Core не должен зависеть от конкретной внешней платформы.

Обычная конфигурация внешних сервисов выполняется в пользовательском разделе
`Настройки → Интеграции`, а не через SQLAdmin. `Integration` описывает отдельное подключение
провайдера и не является глобальным singleton. Возможности провайдера (`payment`,
`fiscalization`, `catalog_projection`, `external_sales_import`, `refunds`) объявляются adapter
registry в коде независимо друг от друга. Текущий AQSI adapter реализует `payment`,
`fiscalization` и `catalog_projection`; payment/fiscalization используются постоянным Sales
checkout, а временный spike остаётся историей физической проверки payload.

Секреты принадлежат `IntegrationCredential` и хранятся только как authenticated ciphertext.
Core использует Fernet из Python-библиотеки `cryptography` (AES-128-CBC + HMAC-SHA256 по
спецификации Fernet). Канонический loader сначала проверяет explicit `MASTER_ENCRYPTION_KEY`,
затем persistent key file, а при первом запуске атомарно создаёт файл под межпроцессной
filesystem-блокировкой. В Docker API и worker используют один volume `core_secrets`.
Master key никогда не пишется в PostgreSQL. Read API не расшифровывает и не сериализует provider
credentials.

Store/TradePoint пока отсутствует в доменной модели. Поэтому Sprint 7.13a не создаёт фиктивный
Store и не добавляет nullable foreign key без владельца. `Integration` допускает несколько строк
одного provider и не имеет provider-wide unique constraint. До появления нескольких подключений
legacy workflows используют единственную AQSI Integration; при неоднозначности операция
останавливается. Следующий минимальный шаг при введении Store — отдельная assignment-модель
`Store ↔ Integration ↔ role`, не изменение Catalog.

### Client Layer

Клиентами Core могут быть:

- SQLAdmin для технического администрирования;
- mobile-first интерфейс сотрудника;
- собственный React/Vue/Flutter frontend;
- интерфейс франшизы;
- Telegram, MAX или другой messenger-клиент;
- партнёрское приложение через публичный API.

Клиенты не должны дублировать бизнес-правила ядра.

---

## Структура проекта

```text
core/

├── docs/
│
├── src/
│   └── core/
│       │
│       ├── catalog/
│       ├── inventory/
│       ├── pricing/
│       ├── media/
│       ├── customers/
│       ├── rental/
│       ├── publishing/
│       ├── integrations/
│       ├── users/
│       ├── activity/
│       ├── audit/
│       ├── shared/
│       │
│       ├── config.py
│       ├── database.py
│       └── main.py
│
├── tests/
├── migrations/
├── storage/
├── docker/
│
├── pyproject.toml
├── uv.lock
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Модульная структура

Каждый бизнес-модуль внутри `src/core/` должен иметь похожую структуру:

```text
catalog/
├── models.py
├── schemas.py
├── service.py
├── repository.py
├── routes.py
└── admin.py
```

Где:

- `models.py` — SQLAlchemy модели;
- `schemas.py` — Pydantic схемы;
- `service.py` — бизнес-логика;
- `repository.py` — работа с БД;
- `routes.py` — HTTP endpoints;
- `admin.py` — SQLAdmin views.

---

## Catalog

Отвечает за:

- Product;
- Variant;
- Category;
- Brand;
- Attributes.

Правило:

```text
Product содержит название, категорию, основное описание и общее фото. Product не имеет SKU,
штрихкода, quantity, закупочной цены, цены продажи или rental commercial terms.

Variant существует всегда. Даже товар без видимых вариантов имеет один default Variant, который
клиентский UI может скрывать, пока он единственный. Variant имеет SKU, ровно один current
operational barcode, variant photo, коммерческие условия и публикации. Quantity существует
только для Variant в контексте Intake и Inventory и изменяется через складской ledger.

Barcode не является identity Variant. Он имеет origin INTERNAL или EXTERNAL, может быть заменён,
а старое значение не остаётся active scanner alias. Удаление EXTERNAL создаёт fresh INTERNAL;
delete-to-regenerate для INTERNAL не является нормальной операцией. Product-level barcode и
ambiguous lookup не поддерживаются. Канонические правила: `docs/14_product_identifiers.md`.
```

Catalog управляет уже существующим каталогом: исправляет карточки, меняет коммерческие данные,
перепечатывает labels и повторяет публикации. Он не является обязательным вторым шагом Intake.
Catalog не владеет Cart, Sale, скидками, оплатой или фискализацией и не должен скрывать checkout
как action внутри карточки товара.

---

## Inventory

Отвечает за:

- склады;
- остатки;
- движения товара;
- приемку;
- списания;
- корректировки.

Правило:

```text
Любое изменение остатка проходит через StockMovement.
```

Источником истины является неизменяемый журнал `StockMovement`. Текущий остаток рассчитывается как сумма движений. Производный кэш или агрегированная таблица могут появиться позже только как оптимизация и не становятся источником истины.

---

## Pricing

Отвечает за цены.

Правило:

```text
Цена не хранится напрямую в Variant.
```

Реализованные типы текущих предложений Variant:

- retail;
- promo;
- rental;
- rental_deposit.

Purchase price принадлежит ReceiptItem как исторический факт поставки. Customer discount в
будущем принадлежит Cart/Sale snapshot и не меняет Pricing.

---

## Sales

Sales — отдельный transaction context и рабочее место POS. Sprint 5.1 реализует `Sale` и
`SaleItem`, состояния `DRAFT`/`CANCELLED`, несколько operator-owned корзин, server-side
persistence и browser-scoped active selection. Catalog остаётся product reference/master data:
Product, Variant, current barcode и ссылки на current Price. Stock projection принадлежит
Inventory и только читается Sales.

Целевой пользовательский поток:

```text
2D HID/keyboard scan current operational barcode
    -> resolve active Variant
    -> add quantity 1 to Cart (repeat scan: quantity +1)
    -> Catalog as visual picker and cart quantity management
    -> payment/fiscalization through provider contracts
    -> confirmed payment creates immutable Inventory SALE movements
    -> itemized fiscalization completes Sale
    -> [future] Customer/Loyalty
```

Неизвестный barcode даёт ясную операторскую ошибку и не создаёт Product или Variant. Sales
сохраняет Catalog snapshots либо manual/open line без Variant. Receipt-level percentage discount
хранится как subtotal/value/amount/total и детерминированные frozen fiscal allocations. Manual
line участвует в payment/receipt, но не в Inventory. Sales является бизнес-источником движений
`SALE` после подтверждённой оплаты, а Inventory остаётся неизменяемым ledger.

Граница AQSI для Sales:

```text
Core owns Cart / Sale and final business outcome
    -> PaymentProvider / FiscalProvider contracts
    -> AQSI cloud now or a future local SDK/device adapter
    -> Core records payment, fiscalization and Sale result
```

Физически подтверждены два integration pattern. Pending Order передаёт itemized order, который
оператор выбирает в меню AQSI; он полезен для pre-created/remote/pickup orders, но не является
предпочтительным обычным checkout. Direct checkout для card/QR запускает acquiring без menu
navigation, а cash подтверждается оператором без acquiring; после любого метода отдельно
выполняется itemized fiscalization. Это целевое направление обычного POS. Временный spike
доказал внешнюю интеграцию, но не является production-архитектурой Sales.

Оба pattern зависят от Internet и AQSI cloud API. Постоянный checkout сохраняет durable duplicate
payment protection, distinct `CANCELED`/`FAILED`/`UNKNOWN` outcomes и состояние acquiring success
при fiscalization failure. Definitive cancel/failure возвращает редактируемый DRAFT, но требует
explicit retry с новым PaymentAttempt; UNKNOWN остаётся заблокированным.
AQSI direct acquiring по умолчанию использует card/QR mode; card-only и QR-only настраиваются в
Integration. Refund/reversal остаются будущей работой. Sales не импортирует AQSI: mapping
находится в integration adapter.

---

## Media

Отвечает за:

- загрузку фото;
- хранение оригиналов;
- генерацию WebP;
- генерацию миниатюр;
- привязку фото к сущностям.

Правило:

```text
Фото обязательно для полноценной приемки товара.
```

Файл хранится в файловой системе, а в базе хранится путь и метаданные.

---

## Rental

Отвечает за:

- физические экземпляры оборудования;
- выдачу;
- возврат;
- осмотр;
- состояние;
- пломбы;
- обслуживание.

Правило:

```text
В аренду выдается RentalAsset, а не Product и не Variant.
```

Rental является первоклассным доменом. Он использует общую основу каталога, изображений, пользователей, аудита и физических идентификаторов, но имеет собственный повторяемый жизненный цикл выдачи, возврата, осмотра и обслуживания.

Customers и Rental являются разными bounded context. `Customer` — получатель имущества, а
`User` — сотрудник или пользователь Core. `Customer != User`.

```text
Product
    ↓
Variant
    ↓
InventoryItem
    ↓
RentalAsset
    ↑
RentalOrderItem
    ↓
RentalOrder
    ↓
Customer
```

Один `RentalAsset` представляет одну физическую вещь и не содержит `quantity`. Inventory
продолжает отвечать за количественный учет и immutable ledger на уровне `Variant`.

`RentalOrder` и `RentalAsset` имеют независимые state machine. Application workflow координирует
их изменения и владеет общей транзакцией.

### Customers

Customers владеет актуальной карточкой и контактами клиента. Rental хранит ссылку на клиента и
snapshot данных, необходимых для исторического представления заказа. Customers не управляет
заказами, экземплярами, платежами или пользовательскими аккаунтами.

Customer — shared business entity, а не часть Rental или набор полей внутри Sale. Существующий
bounded context должен позже обслуживать Sales, Loyalty, purchase history, returns/customer
service и Rental. Начальный Loyalty MVP — постоянная процентная скидка клиента; он проектируется
после устойчивого Cart/Sale context и не меняет Catalog/Pricing.

### Intake и границы транзакции

Rental и Inventory не координируют друг друга напрямую.

Intake является application orchestration workflow и полным рабочим местом подготовки товара до
`ready-for-sale`. Одна пользовательская позиция Intake представляет Product и один или несколько
Variant, поступивших сейчас. Draft допускает добавление, удаление и редактирование вариантов.

Сохранённый Variant с current EAN-13 barcode имеет доступную barcode label до Complete Intake и
независимо от экрана, на котором Variant был создан. Генерация label зависит от сохранённой
identity Variant, а не от наличия StockMovement или завершённой Intake.

Для нового Variant Intake резервирует следующий номер общей Catalog sequence и сохраняет
`reserved_sku` вместе с кандидатом INTERNAL EAN-13. Резервация не создаёт Catalog Variant или
Inventory fact и не переиспользуется после удаления draft. Если оператор не указал внешний код,
этот INTERNAL становится current при Complete. Если внешний код указан, именно он становится
единственным current barcode; зарезервированное внутреннее значение не становится active
fallback. Label до Complete и Catalog после Complete используют один и тот же выбранный current
code, а не временный браузерный идентификатор.

При завершении `CompleteIntakeWorkflow` координирует Catalog, Pricing, Receipt, Inventory и Rental
и является единственным владельцем общей SQL-транзакции. Проведение атомарно фиксирует складские
движения и исторические закупочные факты для каждого Variant; создание отдельных `RentalAsset`
выполняется в той же границе, когда это требуется сценарием.

Доменные сервисы и репозитории Rental и Inventory:

- не вызывают `commit()` или `rollback()` внутри caller-owned transaction;
- выполняют только относящиеся к своему контексту проверки и изменения;
- не импортируют workflow-классы.

Inventory не зависит от Rental. Межконтекстная координация находится в Application/Workflow
layer согласно ADR-002 и ADR-003.

```mermaid
flowchart LR
    Intake["CompleteIntakeWorkflow"] --> Inventory["Inventory"]
    Intake --> Rental["Rental"]
```

После commit отдельная Intake-level операция оркестрирует публикацию всех Variant этой Intake,
которым нужна отправка в AQSI. Результат хранится и показывается отдельно по каждому Variant;
ошибки допускают безопасный retry и не переписывают проведённые Inventory или purchase facts.

### Печать товарных этикеток

Каноническая Product label — векторный PDF 40 × 30 мм с current EAN-13: X-dimension 0,300 мм,
95 bar modules = 28,5 мм, quiet zones по 9 modules, total width 33,9 мм. Она показывает Product,
meaningful Variant, текущую retail price и barcode, но не технический default Variant или SKU.
Отсутствующая цена не превращается в ноль. Профиль Product 58 × 40 сохраняется только для
совместимости; независимые RentalAsset/RENT labels могут иметь оба размера. Канонические правила:
`docs/16_labels.md`.

Печать является отдельным side effect и никогда не входит в транзакцию проведения Intake.

Граница прямой печати:

```text
Intake / Catalog UI
    -> VariantLabelPrintService
    -> CupsPrintingAdapter (`lp` from cups-client)
    -> remote CUPS over IPP
    -> configured printer queue / OS driver
    -> Xprinter
```

`CupsPrintingAdapter` получает готовый одностраничный PDF и copies. API image содержит только
CUPS client, не cupsd и не printer driver. `PRINTING_ENABLED`, `CUPS_SERVER`, `CUPS_USER`,
`CUPS_PRINTER` и optional `CUPS_IPP_VERSION` задаются через environment; defaults не содержат
инфраструктурных адресов или пользователей. Для совместимости с macOS CUPS используется
`-h <server>/version=1.1 -U <user>`. Queue задаётся явно: printer discovery не является
capability check. Adapter запускает фиксированный `lp` безопасным argv без shell, ограничивает
copies 1..500, удаляет временный PDF и возвращает только `submitted`/external job id.
Текущий Docker → remote CUPS → macOS driver → USB Xprinter путь подтверждён; host Print Agent,
USB passthrough и privileged container не нужны. PDF/system print остаётся fallback.

Архитектурный acceptance criterion:

> Если после обычной приёмки оператор вынужден открыть Catalog, чтобы закончить подготовку товара
> к продаже, Intake workflow считается незавершённым.

Связь с будущим Sales пока не является действующей зависимостью. В Sprint 9 Rental может только
вывести экземпляр из прокатного фонда с назначением `SALE`; фактическая продажа будет
координироваться отдельным будущим workflow.

---

## Publishing

Отвечает за состояние публикации во внешние каналы.

Каналы:

- aqsi;
- tilda;
- telegram;
- max;
- wb;
- yamarket.

Правило:

```text
Внешние системы не являются источником истины.
```

Для AQSI Core является business source of truth, а AQSI — внешней catalog/payment/fiscal
интеграцией. Текущая manual publication уже используется в production. Целевая автоматическая
синхронизация выполняет near-immediate event-driven projection и периодическую reconciliation;
reconciliation проверяет ожидаемое состояние Core, а не создаёт двустороннее владение. Все AQSI
операции зависят от AQSI cloud API и доступности Internet.

---

## ImportExport

Целевая, пока не реализованная граница отвечает за:

- импорт CSV из Tilda;
- будущий импорт Excel;
- экспорт данных;
- технические выгрузки.

Правило:

```text
Импортированные товары попадают в Core как draft и требуют проверки.
```

---

## Users

Отвечает за пользователей и роли.

Минимальные роли:

- administrator;
- manager;
- warehouse.

---

## Audit

Отвечает за историю важных действий.

Фиксируются:

- создание товара;
- изменение цены;
- изменение остатка;
- приемка;
- списание;
- публикация;
- аренда;
- возврат;
- изменение состояния оборудования.

---

## Очереди задач

Для долгих операций используется RQ + Redis.

В очередь отправляются:

- обработка изображений;
- генерация этикеток;
- публикация в мессенджеры;
- отправка товаров во внешние системы;
- импорт CSV.

---

## Хранение файлов

На MVP используется локальная файловая система.

Пример структуры:

```text
storage/
├── products/
│   └── SKU/
│       ├── original/
│       ├── web/
│       └── labels/
├── rental/
└── imports/
```

В будущем storage можно заменить на MinIO/S3 без изменения бизнес-логики.

---

## Mobile-first приемка

Первый пользовательский интерфейс, кроме SQLAdmin:

```text
/mobile/intake
```

Требования:

- работает с телефона;
- позволяет сделать фото камерой;
- позволяет выбрать фото из библиотеки;
- позволяет создать Product с одним или несколькими Variant;
- позволяет добавлять, удалять и редактировать Variant в draft;
- позволяет печатать label сохранённого Variant со штрихкодом до завершения;
- не дает завершить приемку без фото;
- атомарно фиксирует Inventory и закупочные факты при завершении;
- после завершения позволяет отправить нужные Variant Intake в AQSI со статусом и retry;
- не публикует товар автоматически.

Camera barcode scanning требует secure context: production deployment mobile UI должен быть
доступен по HTTPS. Ручной ввод и аппаратный scanner не зависят от Camera API и остаются fallback.

---

## SQLAdmin

SQLAdmin используется как техническая админка MVP.

Через нее можно:

- смотреть товары;
- редактировать справочники;
- проверять импорт;
- просматривать движения;
- смотреть публикации;
- управлять пользователями.

SQLAdmin не является финальным пользовательским интерфейсом.

---

## Будущий React frontend

React можно добавить позже.

Он будет использовать тот же FastAPI backend.

Планируемые экраны:

- приемка товара;
- карточка товара;
- склад;
- аренда;
- публикации;
- импорт;
- отчеты.

---

## Docker Compose

MVP должен запускаться одной командой:

```bash
docker compose up
```

Сервисы:

```text
backend
postgres
redis
worker
reverse-proxy
```

## Deployment and bootstrap

Основной способ развёртывания должен быть один, документированный и воспроизводимый. Цель — получить работающий экземпляр Core на подготовленном сервере не более чем за десять минут.

Bootstrap не должен требовать ручного SQL. Подробные цели описаны в [docs/11_deployment_bootstrap.md](11_deployment_bootstrap.md).

## Product documentation

README является точкой входа, но не заменяет пользовательскую, административную, API-, deployment-, franchise- и investor-документацию. Структура описана в [docs/12_product_documentation.md](12_product_documentation.md).

---

## Правила архитектуры

### 1. Product не знает о внешних системах

Плохо:

```
product.publish_to_tilda()"
```

Хорошо:

```
tilda_publisher.publish(variant)"
```

---

### 2. Остатки меняются только через движения

Нельзя напрямую менять количество без StockMovement.

---

### 3. Фото обязательно

Товар или Variant нельзя перевести в статус ready без фото.

---

### 4. Интеграции изолированы

Поломка Tilda Sync не должна ломать Catalog, Inventory или Rental.

---

### 5. Сначала backend, потом frontend

Core должен быть полезен даже без красивого интерфейса.

### 6. Intake завершается ready-for-sale, а не переходом в Catalog

Intake координирует создание Product и Variant, маркировку, проведение Inventory и последующую
публикацию в AQSI. Catalog остаётся самостоятельным интерфейсом управления существующими
карточками и не компенсирует незавершённость Intake.

---

## Что не делаем в MVP

- полноценную ERP;
- бухгалтерию;
- сложную CRM;
- маркетплейс-автоматизацию WB/Яндекс;
- собственный интернет-магазин вместо Tilda;
- сложную систему прав доступа;
- микросервисы;
- Kubernetes.

---

## Следующий шаг

После этого документа создается `05_er_diagram.md`.

ER-диаграмма должна описать только основные связи MVP:

- Product → Variant;
- Variant → Price;
- Variant → Stock;
- Variant → StockMovement;
- Variant → RentalAsset;
- RentalAsset → будущие Rental operations;
- Image → ImageLink;
- Variant → Publication.
