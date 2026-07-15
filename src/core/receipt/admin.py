from __future__ import annotations

from sqladmin import ModelView

from core.receipt.models import Receipt, ReceiptItem


class ReceiptAdmin(ModelView, model=Receipt):
    """SQLAdmin view for receipt references without lifecycle or number edits."""

    name = "Receipt"
    name_plural = "Receipts"
    icon = "fa-solid fa-file-invoice"
    column_list = [Receipt.number, Receipt.supplier_id, Receipt.receipt_date, Receipt.status]
    column_sortable_list = [
        Receipt.number,
        Receipt.supplier_id,
        Receipt.receipt_date,
        Receipt.status,
    ]
    form_excluded_columns = [Receipt.number, Receipt.status]


class ReceiptItemAdmin(ModelView, model=ReceiptItem):
    """SQLAdmin view for draft receipt line references."""

    name = "Receipt item"
    name_plural = "Receipt items"
    icon = "fa-solid fa-list"
    column_list = [
        ReceiptItem.receipt_id,
        ReceiptItem.variant_id,
        ReceiptItem.quantity,
        ReceiptItem.purchase_price,
    ]
    column_sortable_list = [ReceiptItem.receipt_id, ReceiptItem.variant_id, ReceiptItem.quantity]
