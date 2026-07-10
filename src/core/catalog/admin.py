from __future__ import annotations

from sqladmin import ModelView

from core.catalog.models import Category


class CategoryAdmin(ModelView, model=Category):
    """SQLAdmin view for managing catalog categories."""

    name = "Category"
    name_plural = "Categories"
    icon = "fa-solid fa-folder-tree"
    column_list = [
        Category.title,
        Category.slug,
        Category.parent_id,
        Category.sort_order,
        Category.is_active,
    ]
    column_searchable_list = [Category.title, Category.slug]
    column_sortable_list = [Category.sort_order, Category.title, Category.slug]
