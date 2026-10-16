# Sprint 5.20 — Sales Analytics: Revenue Dashboard

Sprint добавляет первый operational dashboard реальных продаж Core: Revenue KPI, дневную
динамику, Product/Variant rankings, weekdays, payment breakdown и точные daily values.

## Реализовано

- только завершённые `SaleStatus.COMPLETED` считаются выручкой;
- historical `Sale.total_amount` и `SaleItem.fiscal_line_total` не зависят от текущей цены;
- business-local Today, 7 days, 30 days, this month и custom period;
- SQL aggregation и стабильный authenticated reporting API;
- mobile-first «Аналитика → Продажи» без нового frontend framework;
- catalog и свободные SaleItem включены в totals с отдельным source breakdown;
- свободные позиции имеют отдельный Top N с консервативной нормализацией имени и никогда не
  объединяются с Catalog по совпадению текста;
- явные ограничения: refunds пока отсутствуют, COGS/profit не симулируются.

## Проверка

Автоматические backend и web UAT tests добавлены. Real-data reconciliation и физический
desktop/iPad/phone UAT остаются перед operational acceptance.
