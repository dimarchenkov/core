from __future__ import annotations

from sqladmin import ModelView

from core.supplier.models import Supplier


class SupplierAdmin(ModelView, model=Supplier):
    """SQLAdmin view for supplier reference data without code edits."""

    name = "Supplier"
    name_plural = "Suppliers"
    icon = "fa-solid fa-truck"
    column_list = [Supplier.code, Supplier.name, Supplier.display_name, Supplier.is_active]
    column_searchable_list = [Supplier.code, Supplier.name, Supplier.display_name]
    column_sortable_list = [Supplier.code, Supplier.name, Supplier.display_name, Supplier.is_active]
    form_excluded_columns = [Supplier.code]
