| Sprint | Review      | ADR              | Статус      |
|--------|-------------|------------------|-------------|
| 08     | ✅ Approved  | ADR-002, ADR-003 | Completed   |
| 09     | ⏳ In progress | —              | In progress |
| 10     | —           | —                | —           |

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
