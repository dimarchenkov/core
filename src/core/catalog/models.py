from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.shared.db import BaseModel, UUIDv7


class Category(BaseModel):
    """Catalog category used to organize future sellable items."""

    __tablename__ = "categories"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    parent_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    parent: Mapped[Category | None] = relationship(
        "Category",
        back_populates="children",
        remote_side="Category.id",
    )
    children: Mapped[list[Category]] = relationship(
        "Category",
        back_populates="parent",
    )


class CatalogProduct(BaseModel):
    """Catalog product family without sellable variant, price, or stock data."""

    __tablename__ = "catalog_products"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    category: Mapped[Category] = relationship("Category")
    variants: Mapped[list[CatalogVariant]] = relationship(
        "CatalogVariant",
        back_populates="product",
    )


class CatalogVariant(BaseModel):
    """Sellable catalog inventory unit that owns its stable system SKU."""

    __tablename__ = "catalog_variants"

    product_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # TODO: Revisit whether this should become variant_name or display_name.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attributes: Mapped[dict[str, str | int | bool]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    product: Mapped[CatalogProduct] = relationship(
        "CatalogProduct",
        back_populates="variants",
    )
