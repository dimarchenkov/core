# Sprint 5.20 — Sales Analytics: Revenue Dashboard

Sprint добавляет operational dashboard бизнес-выручки Core: отдельные Sales и Rental KPI,
дневную динамику, rankings, weekdays, payment breakdown и точные daily values.

## Реализовано

- только завершённые `SaleStatus.COMPLETED` считаются выручкой;
- historical `Sale.total_amount` и `SaleItem.fiscal_line_total` не зависят от текущей цены;
- business-local Today, 7 days, 30 days, this month и custom period;
- SQL aggregation и стабильный authenticated reporting API;
- mobile-first «Аналитика → Продажи» без нового frontend framework;
- catalog и свободные SaleItem включены в totals с отдельным source breakdown;
- свободные позиции имеют отдельный Top N с консервативной нормализацией имени и никогда не
  объединяются с Catalog по совпадению текста;
- Rental revenue признаётся только у `CLOSED RentalOrder` в дату `closed_at` как сумма
  historical base price после скидки и финальной просрочки;
- draft/ready/issued Rental, required/received/applied/refunded deposit и damage/loss
  compensation не входят в revenue;
- business total равен Sales + Rental, операции показываются раздельно, а Top RentalAsset
  использует immutable item snapshots;
- явные ограничения: refunds пока отсутствуют, COGS/profit не симулируются.

## Проверка

Автоматические backend и web UAT tests добавлены. Real-data reconciliation и физический
desktop/iPad/phone UAT остаются перед operational acceptance.
