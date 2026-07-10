from __future__ import annotations

from sqladmin import ModelView

from core.catalog.models import CatalogProduct, CatalogVariant, Category


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


class CatalogProductAdmin(ModelView, model=CatalogProduct):
    """SQLAdmin view for managing catalog product families."""

    name = "Catalog product"
    name_plural = "Catalog products"
    icon = "fa-solid fa-box"
    column_list = [
        CatalogProduct.title,
        CatalogProduct.slug,
        CatalogProduct.category_id,
        CatalogProduct.is_active,
    ]
    column_searchable_list = [CatalogProduct.title, CatalogProduct.slug]
    column_sortable_list = [CatalogProduct.title, CatalogProduct.slug]


class CatalogVariantAdmin(ModelView, model=CatalogVariant):
    """SQLAdmin view for managing sellable catalog variants."""

    name = "Catalog variant"
    name_plural = "Catalog variants"
    icon = "fa-solid fa-cube"
    column_list = [
        CatalogVariant.title,
        CatalogVariant.sku,
        CatalogVariant.product_id,
        CatalogVariant.barcode,
        CatalogVariant.is_active,
    ]
    column_searchable_list = [CatalogVariant.title, CatalogVariant.sku, CatalogVariant.barcode]
    column_sortable_list = [CatalogVariant.title, CatalogVariant.sku]
