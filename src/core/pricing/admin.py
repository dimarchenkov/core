from __future__ import annotations

from sqladmin import ModelView

from core.pricing.models import Price


class PriceAdmin(ModelView, model=Price):
    """Read-only SQLAdmin view for immutable price history."""

    name = "Price"
    name_plural = "Price history"
    icon = "fa-solid fa-tag"
    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        Price.variant_id,
        Price.price_type,
        Price.amount,
        Price.currency,
        Price.effective_from,
        Price.created_by_id,
    ]
    column_sortable_list = [
        Price.variant_id,
        Price.price_type,
        Price.amount,
        Price.effective_from,
    ]
