# Sprint 5.20 — Sales Analytics: Revenue Dashboard

## Назначение

Sales Analytics — read-only слой операционной отчётности поверх постоянного домена Sales.
Данные берутся только из Core; запросы аналитики не обращаются к AQSI и не изменяют Sale.

Основной экран: `Аналитика → Продажи`. Он доступен через API и текущий mobile-first web UI.

## Какие продажи считаются

В метрики входит только `Sale` со следующими фактами одновременно:

- `status = completed`;
- задан `completed_at`;
- `completed_at` попадает в выбранный полуоткрытый UTC-интервал после преобразования локальных
  календарных границ;
- Sale не удалена мягко.

`draft`, `payment_pending`, `paid`, `fiscalization_pending`, `payment_failed`,
`fiscalization_failed` и `cancelled` не считаются выручкой. `paid` является промежуточным
состоянием: Inventory posting и фискализация/явный skip ещё должны довести Sale до `completed`.

## Определения метрик

- **Выручка** — сумма сохранённых `Sale.total_amount` завершённых продаж. Это сумма после
  receipt-level discount.
- **Количество чеков** — количество включённых завершённых Sale.
- **Средний чек** — выручка / количество чеков; при отсутствии чеков возвращается `0.00`.
- **Продано единиц** — сумма `SaleItem.quantity` всех включённых Sale, включая manual/open lines.
- **Catalog source** — `SaleItem.source = catalog` и обязательная `variant_id`.
- **Свободная продажа** — `SaleItem.source = manual`, `variant_id = NULL`; введённое оператором
  название хранится в `display_label_snapshot`.
- **Source revenue** — сумма `SaleItem.fiscal_line_total` соответствующего source. Общая выручка
  по-прежнему берётся из `Sale.total_amount`, поэтому свободные строки не добавляются повторно.
- **Доля свободной продажи по выручке** — `manual fiscal_line_total / Sale.total_amount`;
  при нулевой выручке возвращается `0%`. Отдельно доступна доля по количеству единиц.
- **Top Product** — Catalog SaleItem группируются по устойчивому parent Product identity через
  сохранённую Variant reference. Название выводится из исторического SaleItem snapshot.
- **Top Variant** — Catalog SaleItem группируются по Variant identity; historical label и SKU
  выводятся из SaleItem snapshot.
- **Product/Variant revenue** — сумма `SaleItem.fiscal_line_total`, то есть сохранённая
  детерминированная часть итоговой суммы Sale после скидки.
- **Top Free Sale Item** — только manual/open lines, сгруппированные по вычисляемому ключу имени.
  Catalog Product/Variant rankings остаются строго catalog-only.
- **Метрики дня недели** — сумма по локальным датам выбранного периода. `average revenue`
  делится на число всех наблюдаемых таких дней, включая дни без продаж. `average receipt`
  делится на число завершённых чеков этого дня недели.

Изменение текущей Catalog price или названия после продажи не меняет историческую сумму и label
SaleItem. Архивирование Product/Variant также не исключает SaleItem: reporting queries намеренно
не используют current Catalog lifecycle filters.

### Нормализация свободных наименований

Ключ вычисляется при чтении через `trim/collapse whitespace + casefold`: начальные и конечные
пробелы убираются, повторные внутренние пробелы схлопываются, регистр игнорируется. Цвета,
размеры, числа и слова не удаляются; fuzzy matching, synonyms и AI similarity отсутствуют.

Цена не входит в identity: одинаковое нормализованное имя при разных исторических ценах образует
одну группу, но revenue складывается из фактических `fiscal_line_total`. Display label выбирается
детерминированно из самого свежего исходного snapshot. Хранимый текст SaleItem не меняется.

Совпадение имени свободной позиции с Catalog Product/Variant никогда не объединяет источники:
manual line остаётся в Top Free Sale, а Catalog line — в Top Products/Variants.

## Периоды и timezone

Business timezone берётся из текущей настройки `CORE_RENTAL_TIMEZONE` (по умолчанию
`Europe/Moscow`; это пока общая установленная business-local timezone Core).

- `today` — текущий локальный календарный день, не последние 24 часа;
- `7_days` — сегодня и шесть предыдущих локальных календарных дней;
- `30_days` — сегодня и 29 предыдущих локальных календарных дней;
- `this_month` — с первого локального дня месяца по сегодня;
- `custom` — включительно от `start_date` до `end_date`.

Локальные полуночные границы сначала преобразуются в UTC. Группировка SQL преобразует
`completed_at` обратно в business-local date до `GROUP BY`.

## API

Все endpoints требуют обычной аутентификации Core:

- `GET /api/analytics/sales` — полный read model экрана;
- `GET /api/analytics/sales/summary` — KPI;
- `GET /api/analytics/sales/timeline` — дневная динамика по возрастанию даты;
- `GET /api/analytics/sales/daily` — точная таблица, новые даты первыми;
- `GET /api/analytics/sales/top-products`;
- `GET /api/analytics/sales/top-variants`;
- `GET /api/analytics/sales/top-free-sale-items`;
- `GET /api/analytics/sales/weekdays`.

Общие query parameters: `period`, а для `custom` — `start_date` и `end_date`. Rankings принимают
`ranking=revenue|units`; default top N — 10, управляемый через `limit` до 100.

Расчёты выполняет отдельный `SalesAnalyticsReadService`. Summary, daily groupings и rankings
используют SQL `SUM`, `COUNT` и `GROUP BY`; Sale aggregate и mutation services не расширяются
reporting behavior.

## Текущие ограничения

Returns/refunds в постоянном Sales domain пока отсутствуют. Поэтому dashboard показывает
completed gross/net-of-discount Sales revenue, но не вычитает возмещения. Интерфейс сообщает об
этом явно. После появления refund facts формула должна стать `completed sales - recorded refunds`.

COGS, Gross Profit и Margin не рассчитываются. Текущая Price или последняя закупочная цена не
являются допустимой исторической себестоимостью. Следующий этап должен отдельно выбрать и
реализовать защищаемый cost basis по Intake/Inventory history, например weighted average или FIFO.

Payment breakdown показывается только по постоянному успешному `PaymentAttempt` завершённой Sale:
`Наличные` и `Карта / QR`. AQSI live state для него не запрашивается.

## Проверка

Автоматические тесты покрывают нулевой период, completed/excluded lifecycle states, сумму и
average receipt, item quantities, frozen prices/names, Product aggregation, независимую
сортировку units/revenue, UTC/local midnight, presets/custom period, weekdays, mixed
catalog/manual source totals, receipt de-duplication, conservative normalization, distinct names,
different prices, Catalog name collision и API validation.

Остаётся ручной UAT на реальных данных:

1. Сверить Today KPI со списком завершённых Sale.
2. Завершить новую тестовую продажу и проверить точное изменение KPI после refresh.
3. Сверить source breakdown и Top Products/Variants/Free Sale с известной историей.
4. Изменить текущую Catalog price и убедиться, что историческая выручка не изменилась.
5. Проверить графики, controls и таблицы на desktop, iPad и телефоне.
