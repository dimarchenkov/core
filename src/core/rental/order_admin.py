from __future__ import annotations

from sqladmin import ModelView

from core.rental.models import RentalOrderItemRecord, RentalOrderRecord


class RentalOrderAdmin(ModelView, model=RentalOrderRecord):
    """Read-only SQLAdmin view preserving RentalOrder domain transitions."""

    name = "Rental Order"
    name_plural = "Rental Orders"
    icon = "fa-solid fa-file-contract"
    column_list = [
        RentalOrderRecord.order_number,
        RentalOrderRecord.customer_name_snapshot,
        RentalOrderRecord.status,
        RentalOrderRecord.planned_start_at,
        RentalOrderRecord.planned_return_at,
        RentalOrderRecord.deposit_amount,
    ]
    column_searchable_list = [
        RentalOrderRecord.order_number,
        RentalOrderRecord.customer_name_snapshot,
        RentalOrderRecord.customer_phone_snapshot,
    ]
    column_sortable_list = [
        RentalOrderRecord.order_number,
        RentalOrderRecord.status,
        RentalOrderRecord.planned_start_at,
        RentalOrderRecord.planned_return_at,
    ]
    can_create = False
    can_edit = False
    can_delete = False


class RentalOrderItemAdmin(ModelView, model=RentalOrderItemRecord):
    """Read-only SQLAdmin view for aggregate-owned rental order items."""

    name = "Rental Order Item"
    name_plural = "Rental Order Items"
    icon = "fa-solid fa-screwdriver-wrench"
    column_list = [
        RentalOrderItemRecord.order_id,
        RentalOrderItemRecord.asset_number_snapshot,
        RentalOrderItemRecord.title_snapshot,
        RentalOrderItemRecord.status,
        RentalOrderItemRecord.charged_amount,
    ]
    column_searchable_list = [
        RentalOrderItemRecord.asset_number_snapshot,
        RentalOrderItemRecord.title_snapshot,
    ]
    column_sortable_list = [
        RentalOrderItemRecord.status,
        RentalOrderItemRecord.asset_number_snapshot,
    ]
    can_create = False
    can_edit = False
    can_delete = False
