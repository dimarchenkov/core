# Inventory Engine

## Назначение

Inventory Engine отвечает за учет движения товаров на складе.

Модуль не хранит "остаток" товара напрямую. Источником истины является журнал движений (`StockMovement`).

Текущий остаток рассчитывается как сумма всех движений.

---

# Основные принципы

## Источник истины

Единственным источником истины является журнал движений.

```
Receipt
    ↓
StockMovement
    ↓
Current Balance
```

Поле `stock` в `CatalogVariant` отсутствует.

---

## Неизменяемость

После создания запись `StockMovement` не изменяется.

Ошибки исправляются только созданием нового компенсирующего движения.

Запрещается:

- изменение количества;
- изменение варианта;
- изменение типа движения.

---

## Баланс

Баланс вычисляется как

```
SUM(quantity_delta)
```

для выбранного `CatalogVariant`.

---

# StockMovement

Каждая запись представляет одно изменение количества товара.

Поля:

- id
- variant_id
- movement_type
- quantity_delta
- source_type
- source_id
- notes
- created_at
- created_by_id

---

# MovementType

- RECEIPT
- SALE
- RETURN
- ADJUSTMENT
- WRITE_OFF
- TRANSFER_IN
- TRANSFER_OUT

---

# SourceType

Источник появления движения.

- RECEIPT
- SALE
- INVENTORY
- SYSTEM

---

# Receipt Posting

Receipt в статусе Draft не изменяет склад.

После выполнения Posting:

Receipt(Draft)
│
▼
Receipt(Posted)
│
▼
StockMovement (+N)

---

# Receipt Cancellation

При отмене Receipt исходные движения не удаляются.

Создаются компенсирующие движения с противоположным знаком.

Пример

```
+15 Receipt

↓

-15 Receipt Cancel
```

---

# Инварианты

Всегда выполняются следующие правила.

- StockMovement никогда не изменяется.
- Receipt Draft не влияет на остатки.
- Только Posted Receipt создаёт движения.
- Баланс вычисляется только по движениям.
- Все движения имеют источник.
- Все движения относятся к одному CatalogVariant.