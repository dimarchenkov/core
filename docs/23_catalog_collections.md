# Catalog Collections

## Назначение

Collection — лёгкая Variant-level группировка для витрин, подборок и будущих каналов
публикации. Она не меняет владение Catalog:

- Category отвечает «что это за Product?» и остаётся на Product;
- Collection отвечает «где или как показать Variant?» и связывает только concrete Variant;
- Product нельзя добавить в Collection целиком неявно.

Примеры STATIC Collection: «На кассе», «Подарки». Примеры DYNAMIC Collection: «Всё по 50»,
«До 100 ₽», «Активные товары до 100 ₽».

## Типы и жизненный цикл

`STATIC` хранит явные `CollectionMembership`. Пара `(collection_id, variant_id)` уникальна;
удаление membership не меняет Product или Variant. Один Variant может состоять в нескольких
STATIC Collection и одновременно совпадать с несколькими DYNAMIC Collection.

`DYNAMIC` хранит `CollectionRule`, но не хранит вычисленные membership. Тип Collection после
создания неизменяем: переход STATIC ↔ DYNAMIC заблокирован, чтобы не выполнять скрытую
разрушительную конверсию.

`is_active=false` скрывает Collection из обычного Catalog sidebar и Variant card, но сохраняет
её в административном списке и позволяет просмотреть/исправить. Это отличается от удаления:
удаление Collection удаляет только принадлежащие ей Rule и Membership. Product, Variant,
Category, Inventory, Sales, Rental и AQSI данные не меняются.

Slug — внутренний стабильный идентификатор. Оператор его не вводит: сервер транслитерирует
название (`Всё за 50` → `vse-za-50`) и при совпадении добавляет детерминированный числовой
суффикс. Переименование Collection не меняет уже созданный slug.

## Правила

Все правила объединяются только через AND. Разрешены четыре поля:

| Поле | Источник текущего значения | Операторы | Значение |
|---|---|---|---|
| PRICE | последняя действующая `PriceType.RETAIL` | EQ, LT, LTE, GT, GTE | Decimal RUB money |
| CATEGORY | `CatalogProduct.category_id` | EQ | существующая active Category UUID |
| STOCK | сумма immutable `StockMovement.quantity_delta` | EQ, LT, LTE, GT, GTE | целое число |
| IS_ACTIVE | operational state Product + Variant | EQ | boolean |

Произвольные имена полей, SQL, OR, nested groups и expression tree отсутствуют. Архивные
(`deleted_at`) Product/Variant исключаются из DYNAMIC evaluation; non-operational Product или
Variant остаётся доступен правилу `IS_ACTIVE EQ false`. `IS_ACTIVE EQ true` требует активных
Product и Variant. Отсутствующая текущая retail price не совпадает с numeric comparison.

## Вычисление и counts

STATIC count — количество сохранённых membership. DYNAMIC count и список выполняют один и тот
же live SQL query по Price и Inventory источникам истины. Изменение текущей цены или новый
StockMovement сразу меняют DYNAMIC результат без записи membership и без cache/materialization.

UI preview применяет несохранённые валидированные правила, возвращает точный count и только
ограниченный первый набор Variant. Детальная DYNAMIC Collection также показывает текущий
результат, а не snapshot.

## Application и UI boundary

`CollectionService.get_collection_variants(collection_id)` — единая query boundary для
будущих Publishing/storefront consumers. Текущий Publishing/AQSI/Tilda flow не изменён.

Активные Collections показаны в обычном Catalog sidebar как «Подборки» рядом с Category.
Выбор подборки фильтрует существующие Product cards на сервере и оставляет внутри Product только
совпавшие Variant. Карточки не дублируются: сохраняются обычные фото, цена, остаток, AQSI,
навигация и кнопка добавления в Sale. Search, status, mode и sort продолжают работать; в UI
Category и Collection используются как взаимоисключающие primary filters.

DYNAMIC result и поиск Variant внутри управления Collection сохраняют Product-local порядок
Variant. STATIC Collection имеет отдельный merchandising-порядок `CollectionMembership` и не
перезаписывается порядком Product.

«Управление подборками» открывает административный список с созданием, редактированием,
включением/отключением и явным подтверждаемым удалением. STATIC detail поддерживает поиск
concrete Variant, добавление и удаление. DYNAMIC detail показывает AND-rules и live result;
editor содержит count preview.

В карточке Variant активные STATIC Collection доступны как checkboxes. Совпавшие активные
DYNAMIC Collection показаны отдельно и read-only. На узком экране список, rule rows и действия
складываются в одну колонку без horizontal scroll.

## Отложенные расширения

- OR/nested rule groups и новые rule fields;
- materialized membership или cache только после измеренной проблемы производительности;
- drag-and-drop ordering STATIC membership;
- подключение Collection к конкретным Publishing/storefront policies;
- policy для запрета удаления после появления внешних Collection references.
