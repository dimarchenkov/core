# Core Modules

## Общая архитектура

Core состоит из независимых модулей.

Каждый модуль отвечает только за свою область ответственности и взаимодействует с другими через внутреннее API.

```
Core

├── Catalog
├── Inventory
├── Media
├── Pricing
├── Barcode
├── Suppliers
├── Warehouse
├── Customers
├── Sales
├── Rental
├── Publishing
├── Messaging
├── Users
├── Audit
└── ImportExport
```

---

# Catalog

Отвечает за каталог товаров.

Основные сущности:

- Product
- Variant
- Category
- Brand

---

# Inventory

Учет остатков.

Основные задачи:

- приемка
- списание
- инвентаризация
- перемещение
- история движения

---

# Media

Хранение исходных изображений и универсальных `ImageLink`.

Сейчас поддерживает:

- проверенные JPEG/PNG/WebP;
- нормализацию HEIC/HEIF и JPEG/MPO в browser-compatible WebP;
- primary/gallery links для Catalog и condition evidence для Rental;
- защищённую выдачу source.

Полный master/web/thumb pipeline, документы и инструкции остаются будущими расширениями.

---

# Pricing

Хранение append-only истории цен Variant и вычисление текущего значения.

Типы цен:

- закупочная
- розничная
- акционная
- арендная

Закупочная цена конкретной поставки остаётся историческим фактом ReceiptItem и не является
текущей Price Variant.

---

# Barcode

Правила операционных идентификаторов Catalog и входов сканера.

Поддерживает:

- стабильный SKU;
- ровно один current operational barcode Variant с origin INTERNAL или EXTERNAL;
- EAN-13/EAN-8/UPC-A/Code 128 validation и scanner input;
- QR — только для отдельных будущих use case, не как второй active barcode.

Генерация Product и RentalAsset labels остаётся отдельной output capability. Barcode не является
identity Variant; заменённые значения не остаются активными aliases.

---

# Suppliers

Поставщики.

Хранит:

- контакты
- историю поставок
- комментарии

---

# Warehouse

Целевая граница информации о складах. Сейчас Inventory использует один складской контекст без
полноценного Warehouse UI.

Позже:

- несколько складов
- зоны хранения
- ячейки

---

# Rental

Учет конкретных физических экземпляров, предназначенных для аренды.

Основная сущность:

- RentalAsset

Отвечает за:

- стабильную идентичность физического экземпляра;
- назначение, физическое состояние и операционную доступность;
- выдачу и возврат;
- обслуживание;
- вывод экземпляра из аренды для будущей продажи;
- окончательное списание без удаления истории.

Не отвечает за:

- количественный складской учет и движения Inventory;
- приходование товара;
- продажи;
- договоры, бронирование, залоги и платежи в Sprint 9.

Создание RentalAsset во время завершения Intake координирует workflow-слой. Rental не управляет
Inventory напрямую.

---

# Customers

Учет актуальных карточек клиентов.

Отвечает за:

- карточки клиентов;
- контактные данные;
- поиск;
- историю изменения актуальных данных клиента.

Не отвечает за:

- аренду;
- `RentalAsset`;
- платежи;
- пользовательские аккаунты Core.

Customers передает Rental идентичность и актуальные данные клиента. Rental сохраняет собственный
snapshot этих данных в заказе и координирует операции с Inventory только через workflow-слой.

Customer является общей бизнес-сущностью для Rental, будущих Sales, Loyalty, purchase history и
returns/customer service. Он не моделируется как набор полей внутри Sale. Существующая Customer
Foundation переиспользуется; будущий Loyalty MVP добавляет только постоянную процентную скидку и
не меняет Catalog/Pricing.

```text
Customers
    ↓
Rental
    ↓
Inventory
```

---

# Sales

Серверный transaction context и mobile-first POS workspace. Sprint 5.1 реализует несколько
operator-owned `DRAFT Sale`, отмену, server-side cart persistence, commercial snapshots,
переключение active Sale в browser workspace, Catalog add flow и context-aware HID scanner.
Sprint 5.2 добавляет receipt-level percentage discount, manual/open SaleItem, durable cash/card/QR
checkout, PaymentAttempt, Fiscalization, recovery и completion.

Отвечает за:

- Cart и Sale lifecycle;
- Catalog/manual позиции и снимки текущей базовой цены;
- subtotal, процентную скидку, deterministic fiscal allocation и payable total;
- payment/fiscalization state через generic provider ports;
- создание immutable Inventory `SALE` movements после подтверждённой оплаты;
- будущий customer selection/Loyalty.

Sales использует Catalog для Variant reference/current lookup и Pricing для base current price,
но не является действием Catalog. AQSI остаётся внешним acquiring/fiscal adapter. Первый-class
POS input — hardware scanner в HID/keyboard mode; повторный scan увеличивает quantity. Intake
имеет приоритетный локальный scanner context. Подробные решения Sprint 5.1 описаны в
[`21_sales_workspace.md`](21_sales_workspace.md).
Checkout decisions и failure semantics описаны в
[`22_checkout_payment_fiscalization.md`](22_checkout_payment_fiscalization.md).

---

# Publishing

Публикация товаров во внешние каналы.

Реализовано:

- ручная Core → AQSI publication со статусом и retry.

Запланировано:

- автоматическая AQSI synchronization/reconciliation;
- Tilda;
- Wildberries
- Яндекс Маркет

---

# Messaging

Публикация сообщений.

На первом этапе:

- Telegram
- MAX

Позже:

- Email
- VK

---

# Users

Пользователи системы.

Текущая Identity Lite различает:

- обычного активного пользователя;
- Administrator (`is_admin`);
- временный аварийный Superuser, управляемый только локальным CLI с аудитом.

Отдельные роли Manager/Warehouse пока не реализованы.

---

# Audit

Журнал действий.

Фиксируются:

- создание
- изменение
- удаление
- публикация
- приемка
- аренда

---

# ImportExport

Импорт и экспорт данных.

Запланировано; отдельный рабочий ImportExport lifecycle пока не реализован:

- импорт CSV из Tilda
- экспорт данных
- резервное копирование

Позже:

- Excel
- CommerceML
