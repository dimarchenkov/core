# Sprint 4.14 — Rental Tariffs

**Status:** implemented; physical Puzzi/iPhone UAT pending.

**Epic 4 status:** ✅ Operational milestone completed; active follow-up in progress.

Sprint 4.14 закрывает отложенный пробел тарифов, не переоткрывая завершённые Rental foundation,
orders, issue/return, history, maintenance и economics. Формат номера — `Epic.Sprint`: Epic 4 и
глобальный Sprint 14. Исторические Sprint 9–10G не переименовываются.

## Аудит существующей модели

- отдельного `RentalVariant` нет; арендуемое предложение — существующий `CatalogVariant`;
- `RentalAssetRecord.variant_id` связывает физические экземпляры с Variant;
- `RentalOrderRecord` хранит фактический залог сделки, а `RentalOrderItemRecord` — `agreed_price`
  и `charged_amount`;
- текущие базовые RENTAL и RENTAL_DEPOSIT являются append-only facts в Pricing;
- выдача/возврат, UI аренды, settings architecture, Alembic и тестовые паттерны переиспользованы,
  существующие сущности не дублировались.

## Принятые решения

- `RentalTariffRecord` — отдельная сущность, принадлежащая ровно одному `CatalogVariant`; общих
  many-to-many шаблонов тарифов нет.
- Поддержаны DURATION с положительным `duration_minutes` и SLOT с дневными границами
  `slot_start < slot_end`. Overnight SLOT намеренно отклоняется как неоднозначный.
- Цена и обеспечительный платёж хранятся как `Numeric(12, 2)`/`Decimal`, float API отклоняется.
- Приоритет залога: явный tariff deposit → копия текущего Variant RENTAL_DEPOSIT при создании
  версии → `NULL`. Глобального system default в Core нет.
- `grace_minutes` и `requires_agreement` только сохраняются; overdue charging и блокировка выдачи
  не входят в Sprint 4.14.
- Канонический selectable query использует ACTIVE lifecycle, `valid_from` и включительную
  `valid_to`; будущие и истёкшие версии не возвращаются.
- Любое meaningful edit всегда создаёт новую строку той же lineage с монотонным `version`. Это
  выбранная безопасная политика вместо определения использования по ещё не существующему Rental
  snapshot.
- Будущая версия автоматически завершает текущий период предыдущей датой и не вытесняет её до
  `valid_from`. Немедленная версия переводит предыдущую в SUPERSEDED.
- Единственную никогда не использованную версию можно hard-delete. `mark_used()` ставит
  `locked_at`; использованные и имеющие историю версии hard-delete не допускают. Обычная
  пользовательская операция — archive с сохранением истории.
- `to_snapshot()` выдаёт детерминированные JSON-safe данные для Sprint 4.15: tariff id/version,
  name/type, exact money strings, period, grace, deposit и agreement requirement.
- `format_terms()` является единым источником будущего `{{ tariff_terms }}`.
- Версионность сохраняет основу для будущей integrity attestation, но не заявляется как
  криптографическая подпись или юридическое доказательство.

## UI и API

Тарифы управляются в обычной карточке арендуемого Variant, без SQLAdmin. Доступны:

- карточки текущих/будущих тарифов;
- создание DURATION или SLOT без JSON и raw ID;
- изменение с понятным уведомлением о новой версии;
- история v1/v2/… и архивирование;
- responsive single-column layout, touch actions, native date/time inputs без horizontal overflow.

API:

- `GET/POST /api/rental/variants/{variant_id}/tariffs`;
- `PUT /api/rental/tariffs/{tariff_id}`;
- `GET /api/rental/tariffs/{tariff_id}/history`;
- `POST /api/rental/tariffs/{tariff_id}/archive`;
- защищённый hard-delete неиспользованного одиночного тестового тарифа.

## Manual UAT — Karcher Puzzi 10/1

После применения миграции `0038_rental_tariffs` выполнить в карточке реального Variant:

1. Создать «Утро»: SLOT 09:00–14:00, 1 900 ₽, grace 30 мин, deposit 5 000 ₽, договор обязателен.
2. Создать «Вечер»: SLOT 15:00–20:00, 1 900 ₽, grace 30 мин, deposit 5 000 ₽, договор обязателен.
3. Создать «5 часов»: DURATION 5:00, актуальная выбранная цена, grace 30 мин, deposit 5 000 ₽,
   договор обязателен.
4. Изменить цену «Утро» и проверить сохранение старой версии в истории.
5. Создать будущую версию и проверить, что до `valid_from` она есть в управлении/истории, но не
   возвращается selectable query.
6. На физическом iPhone проверить карточки, type-specific inputs, время, историю, touch actions и
   отсутствие горизонтального скролла.

UAT намеренно не выполняется автоматически и остаётся точкой остановки Sprint 4.14.

## Следующий scope

Sprint 4.15 добавит Rental Draft & Tariff Snapshot и вызовет `mark_used()` для выбранной версии.
Выдача, договоры, возврат и overdue calculation остаются соответственно в Sprint 4.16–4.18.
