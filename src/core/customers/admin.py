from __future__ import annotations

from sqladmin import ModelView

from core.customers.models import CustomerRecord


class CustomerAdmin(ModelView, model=CustomerRecord):
    """SQLAdmin view for current customer contacts without physical deletion."""

    name = "Customer"
    name_plural = "Customers"
    icon = "fa-solid fa-address-card"
    column_list = [
        CustomerRecord.customer_number,
        CustomerRecord.full_name,
        CustomerRecord.phone,
        CustomerRecord.email,
        CustomerRecord.status,
    ]
    column_searchable_list = [
        CustomerRecord.customer_number,
        CustomerRecord.full_name,
        CustomerRecord.phone,
        CustomerRecord.email,
    ]
    column_sortable_list = [
        CustomerRecord.customer_number,
        CustomerRecord.full_name,
        CustomerRecord.status,
    ]
    form_excluded_columns = [CustomerRecord.customer_number]
    can_delete = False
