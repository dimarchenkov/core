from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.catalog.barcode import InternalBarcodeGenerator
from core.catalog.barcodes import BarcodeSource, normalize_barcode
from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.catalog.repository import (
    CatalogProductRepository,
    CatalogVariantBarcodeRepository,
    CatalogVariantRepository,
    CategoryRepository,
)
from core.catalog.schemas import (
    CatalogProductCreate,
    CatalogProductUpdate,
    CatalogVariantCreate,
    CatalogVariantUpdate,
    CategoryCreate,
    CategoryQuickCreate,
    CategoryUpdate,
)
from core.catalog.sku import SkuGenerator
from core.shared.db import UUIDv7, generate_uuid_v7


class CategoryNotFoundError(Exception):
    """Raised when a category cannot be found."""


class CategorySlugAlreadyExistsError(Exception):
    """Raised when a category slug is already used by another category."""


class CategoryParentError(Exception):
    """Raised when a category parent is invalid."""


class CategoryArchiveBlockedError(Exception):
    """Raised when a Category still owns live Products or child Categories."""

    def __init__(self, *, product_count: int, child_count: int) -> None:
        """Retain real dependency counts for clear operator feedback."""
        super().__init__("Category archive is blocked by live dependencies.")
        self.product_count = product_count
        self.child_count = child_count


class CategoryRestoreParentError(Exception):
    """Raised when an archived Category's former parent is unavailable."""

    def __init__(self, parent_title: str | None) -> None:
        """Retain the former parent title for operator feedback."""
        super().__init__("Category parent is unavailable for restore.")
        self.parent_title = parent_title


class CatalogProductNotFoundError(Exception):
    """Raised when a catalog product cannot be found."""


class CatalogProductSlugAlreadyExistsError(Exception):
    """Raised when a slug is already used by a non-deleted product."""


class CatalogProductCategoryError(Exception):
    """Raised when a product category is missing, deleted, or inactive."""


class CatalogVariantNotFoundError(Exception):
    """Raised when a catalog variant cannot be found."""


class CatalogVariantProductError(Exception):
    """Raised when a variant product is missing, deleted, or inactive."""


class CatalogVariantBarcodeConflictError(Exception):
    """Raised when one barcode is already assigned to another Variant."""


class CatalogVariantBarcodeDeleteError(Exception):
    """Raised when deletion is requested for a current INTERNAL barcode."""


class CatalogRestoreNotFoundError(Exception):
    """Raised when a restore target no longer exists."""


class CatalogProductRestoreCategoryError(Exception):
    """Raised when an archived Product's Category cannot accept a restored Product."""

    def __init__(self, category_title: str | None) -> None:
        """Retain the unavailable Category title for operator feedback."""
        super().__init__("Product category is unavailable.")
        self.category_title = category_title


class CatalogProductRestoreSlugConflictError(Exception):
    """Raised when another active Product owns the archived Product's slug."""


class CatalogVariantRestoreProductError(Exception):
    """Raised when an archived Variant's parent Product is unavailable."""


class CatalogVariantRestoreSkuConflictError(Exception):
    """Raised when another active Variant owns the archived Variant's SKU."""

    def __init__(self, sku: str) -> None:
        """Retain the conflicting SKU for operator feedback."""
        super().__init__("Variant SKU is already active.")
        self.sku = sku


class CatalogVariantRestoreBarcodeConflictError(Exception):
    """Raised when another active Variant owns the archived Variant's barcode."""

    def __init__(self, barcode: str) -> None:
        """Retain the conflicting barcode for operator feedback."""
        super().__init__("Variant barcode is already active.")
        self.barcode = barcode


class CategoryService:
    """Business operations for catalog categories."""

    def __init__(self, session: Session) -> None:
        """Create a service using the given database session."""
        self._session = session
        self._repository = CategoryRepository(session)

    def list_categories(self, archive_status: str = "active") -> Sequence[Category]:
        """Return active, archived, or all Categories for the requested UI context."""
        return self._repository.list(archive_status)

    def get_category(self, category_id: UUIDv7) -> Category:
        """Return one category or raise when it does not exist."""
        category = self._repository.get(category_id)
        if category is None:
            raise CategoryNotFoundError
        return category

    def create_category(self, data: CategoryCreate, *, actor_id: UUIDv7 | None = None) -> Category:
        """Create a category after validating slug and parent references."""
        self._ensure_slug_available(data.slug)
        self._ensure_parent_exists(data.parent_id)

        category = Category(
            title=data.title,
            slug=data.slug,
            parent_id=data.parent_id,
            sort_order=data.sort_order,
            is_active=data.is_active,
            created_by_id=actor_id,
        )
        self._repository.add(category)
        self._session.flush()
        return category

    def create_named_category(
        self,
        data: CategoryQuickCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Category:
        """Create an operator-named Category with an internal unique slug."""
        return self.create_category(
            CategoryCreate(
                title=data.title,
                slug=f"category-{generate_uuid_v7()}",
                parent_id=data.parent_id,
            ),
            actor_id=actor_id,
        )

    def update_category(
        self,
        category_id: UUIDv7,
        data: CategoryUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Category:
        """Update a category after validating changed fields."""
        category = self._repository.get_any_for_update(category_id)
        if category is None or category.deleted_at is not None:
            raise CategoryNotFoundError
        changes = data.model_dump(exclude_unset=True)

        if "slug" in changes:
            self._ensure_slug_available(data.slug, current_category_id=category_id)
            category.slug = data.slug

        if "parent_id" in changes:
            self._ensure_parent_exists(data.parent_id, current_category_id=category_id)
            category.parent_id = data.parent_id

        if data.title is not None:
            category.title = data.title
        if data.sort_order is not None:
            category.sort_order = data.sort_order
        if data.is_active is not None:
            category.is_active = data.is_active
        if actor_id is not None:
            category.updated_by_id = actor_id

        self._session.flush()
        return category

    def delete_category(self, category_id: UUIDv7, *, actor_id: UUIDv7 | None = None) -> None:
        """Archive an empty leaf Category while preserving historical identity."""
        category = self._repository.get_any_for_update(category_id)
        if category is None or category.deleted_at is not None:
            raise CategoryNotFoundError
        product_count = self._repository.count_active_products(category_id)
        child_count = self._repository.count_live_children(category_id)
        if product_count or child_count:
            raise CategoryArchiveBlockedError(
                product_count=product_count,
                child_count=child_count,
            )
        category.soft_delete(actor_id)
        self._session.flush()

    def restore_category(
        self,
        category_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Category:
        """Restore one Category without silently changing its former parent."""
        category = self._repository.get_any_for_update(category_id)
        if category is None:
            raise CategoryNotFoundError
        if category.deleted_at is None:
            return category
        if category.parent_id is not None:
            parent = self._repository.get_any_for_update(category.parent_id)
            if parent is None or parent.deleted_at is not None or not parent.is_active:
                raise CategoryRestoreParentError(parent.title if parent else None)
            self._ensure_parent_exists(parent.id, current_category_id=category.id)
        category.restore()
        if actor_id is not None:
            category.updated_by_id = actor_id
        self._session.flush()
        return category

    def _ensure_slug_available(
        self,
        slug: str | None,
        current_category_id: UUIDv7 | None = None,
    ) -> None:
        """Raise when a slug belongs to another active category."""
        if slug is None:
            return

        category = self._repository.get_by_slug(slug)
        if category is not None and category.id != current_category_id:
            raise CategorySlugAlreadyExistsError

    def _ensure_parent_exists(
        self,
        parent_id: UUIDv7 | None,
        current_category_id: UUIDv7 | None = None,
    ) -> None:
        """Reject missing, unavailable, self, descendant, or already-cyclic parents."""
        if parent_id is None:
            return
        visited: set[UUIDv7] = set()
        cursor_id: UUIDv7 | None = parent_id
        while cursor_id is not None:
            if cursor_id == current_category_id or cursor_id in visited:
                raise CategoryParentError
            visited.add(cursor_id)
            parent = self._repository.get_any_for_update(cursor_id)
            if parent is None or parent.deleted_at is not None or not parent.is_active:
                raise CategoryParentError
            cursor_id = parent.parent_id


class CatalogProductService:
    """Business operations for catalog product families."""

    def __init__(self, session: Session) -> None:
        """Create a service using the given database session."""
        self._session = session
        self._repository = CatalogProductRepository(session)
        self._category_repository = CategoryRepository(session)

    def list_products(self) -> Sequence[CatalogProduct]:
        """Return all non-deleted catalog products ordered for display."""
        return self._repository.list()

    def get_product(self, product_id: UUIDv7) -> CatalogProduct:
        """Return one catalog product or raise when it does not exist."""
        product = self._repository.get(product_id)
        if product is None:
            raise CatalogProductNotFoundError
        return product

    def create_product(
        self,
        data: CatalogProductCreate,
        *,
        actor_id: UUIDv7 | None = None,
        is_test: bool = False,
    ) -> CatalogProduct:
        """Validate and stage a product for the command owner to commit."""
        self._ensure_slug_available(data.slug)
        self._ensure_category_is_active(data.category_id)

        product = CatalogProduct(
            **data.model_dump(),
            is_test=is_test,
            created_by_id=actor_id,
        )
        self._repository.add(product)
        self._session.flush()
        return product

    def update_product(
        self,
        product_id: UUIDv7,
        data: CatalogProductUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CatalogProduct:
        """Update a catalog product after validating supplied business fields."""
        product = self.get_product(product_id)
        changes = data.model_dump(exclude_unset=True)

        if "slug" in changes:
            self._ensure_slug_available(data.slug, current_product_id=product_id)
        if "category_id" in changes:
            self._ensure_category_is_active(data.category_id)
        for field, value in changes.items():
            setattr(product, field, value)
        if actor_id is not None:
            product.updated_by_id = actor_id

        self._session.flush()
        return product

    def delete_product(self, product_id: UUIDv7, *, actor_id: UUIDv7 | None = None) -> None:
        """Soft-delete a catalog product while preserving its business history."""
        product = self.get_product(product_id)
        product.soft_delete(actor_id)
        self._session.flush()

    def _ensure_slug_available(
        self,
        slug: str | None,
        current_product_id: UUIDv7 | None = None,
    ) -> None:
        """Raise when a slug belongs to another non-deleted catalog product."""
        if slug is None:
            return
        product = self._repository.get_by_slug(slug)
        if product is not None and product.id != current_product_id:
            raise CatalogProductSlugAlreadyExistsError

    def _ensure_category_is_active(self, category_id: UUIDv7 | None) -> None:
        """Raise when a product category is unavailable for catalog assignment."""
        if category_id is None:
            raise CatalogProductCategoryError
        category = self._category_repository.get(category_id)
        if category is None or not category.is_active:
            raise CatalogProductCategoryError


class CatalogVariantService:
    """Business operations for sellable catalog variants."""

    def __init__(self, session: Session) -> None:
        """Create a service using the given database session."""
        self._session = session
        self._repository = CatalogVariantRepository(session)
        self._barcode_repository = CatalogVariantBarcodeRepository(session)
        self._product_repository = CatalogProductRepository(session)

    def list_variants(self) -> Sequence[CatalogVariant]:
        """Return all non-deleted catalog variants ordered for display."""
        return self._repository.list()

    def get_variant(self, variant_id: UUIDv7) -> CatalogVariant:
        """Return one catalog variant or raise when it does not exist."""
        variant = self._repository.get(variant_id)
        if variant is None:
            raise CatalogVariantNotFoundError
        return variant

    def find_variant_by_barcode(self, barcode: str) -> CatalogVariant:
        """Resolve an exact scanner value to one non-archived catalog variant."""
        variant = self._repository.get_active_by_barcode(normalize_barcode(barcode))
        if variant is None:
            raise CatalogVariantNotFoundError
        return variant

    def create_variant(
        self,
        data: CatalogVariantCreate,
        *,
        actor_id: UUIDv7 | None = None,
        reserved_sku: str | None = None,
        reserved_barcode: str | None = None,
    ) -> CatalogVariant:
        """Validate and stage a generated variant for the command owner to commit."""
        self._ensure_product_is_active(data.product_id)
        if (reserved_sku is None) != (reserved_barcode is None):
            raise ValueError("Reserved SKU and barcode must be supplied together.")
        if reserved_sku is None:
            identifier_number = self._repository.next_sku_number()
            sku = SkuGenerator.generate(identifier_number)
            internal_barcode = InternalBarcodeGenerator.generate(identifier_number)
        else:
            sku = reserved_sku
            internal_barcode = reserved_barcode
        manufacturer_barcode = data.manufacturer_barcode
        barcode = manufacturer_barcode or internal_barcode
        source = (
            BarcodeSource.MANUFACTURER
            if manufacturer_barcode is not None
            else BarcodeSource.INTERNAL
        )
        if self._repository.get_by_barcode(barcode) is not None:
            raise CatalogVariantBarcodeConflictError
        if self._barcode_repository.get_by_value(barcode) is not None:
            raise CatalogVariantBarcodeConflictError
        variant_data = data.model_dump(exclude={"manufacturer_barcode"})
        variant = CatalogVariant(
            sku=sku,
            barcode=barcode,
            barcode_source=source,
            **variant_data,
            created_by_id=actor_id,
        )
        self._repository.add(variant)
        self._session.flush()
        self._register_barcode(
            variant,
            barcode,
            source,
            actor_id=actor_id,
        )
        self._session.flush()
        return variant

    def register_manufacturer_barcode(
        self,
        variant_id: UUIDv7,
        value: str,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CatalogVariantBarcode:
        """Compatibility facade: replace, rather than append, the operational barcode."""
        variant = self.replace_barcode(variant_id, value, actor_id=actor_id)
        current = self._barcode_repository.get_active_for_variant(variant.id)
        if current is None:
            raise RuntimeError("Current barcode history row is missing.")
        return current

    def replace_barcode(
        self,
        variant_id: UUIDv7,
        value: str,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CatalogVariant:
        """Atomically replace the current barcode with one external operational code."""
        variant = self._repository.get_for_update(variant_id)
        if variant is None:
            raise CatalogVariantNotFoundError
        normalized = normalize_barcode(value)
        current = self._barcode_repository.get_active_for_variant(variant.id)
        if normalized == variant.barcode:
            variant.barcode_source = BarcodeSource.MANUFACTURER
            if current is not None:
                current.source = BarcodeSource.MANUFACTURER
                current.updated_by_id = actor_id
            variant.updated_by_id = actor_id
            self._session.flush()
            return variant
        if self._repository.get_by_barcode(normalized) is not None:
            raise CatalogVariantBarcodeConflictError
        if self._barcode_repository.get_by_value(normalized) is not None:
            raise CatalogVariantBarcodeConflictError
        if current is not None:
            current.soft_delete(actor_id)
        variant.barcode = normalized
        variant.barcode_source = BarcodeSource.MANUFACTURER
        variant.updated_by_id = actor_id
        self._register_barcode(variant, normalized, BarcodeSource.MANUFACTURER, actor_id=actor_id)
        self._session.flush()
        return variant

    def delete_external_barcode(
        self,
        variant_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CatalogVariant:
        """Retire one EXTERNAL barcode and atomically install a fresh INTERNAL EAN-13."""
        variant = self._repository.get_for_update(variant_id)
        if variant is None:
            raise CatalogVariantNotFoundError
        if variant.barcode_source is BarcodeSource.INTERNAL:
            raise CatalogVariantBarcodeDeleteError
        current = self._barcode_repository.get_active_for_variant(variant.id)
        if current is not None:
            current.soft_delete(actor_id)
        while True:
            identifier_number = self._repository.next_sku_number()
            barcode = InternalBarcodeGenerator.generate(identifier_number)
            if self._barcode_repository.get_by_value(barcode) is None:
                break
        variant.barcode = barcode
        variant.barcode_source = BarcodeSource.INTERNAL
        variant.updated_by_id = actor_id
        self._register_barcode(variant, barcode, BarcodeSource.INTERNAL, actor_id=actor_id)
        self._session.flush()
        return variant

    def update_variant(
        self,
        variant_id: UUIDv7,
        data: CatalogVariantUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CatalogVariant:
        """Update mutable variant fields without changing the generated SKU."""
        variant = self.get_variant(variant_id)
        changes = data.model_dump(exclude_unset=True)
        if "product_id" in changes:
            self._ensure_product_is_active(data.product_id)
        for field, value in changes.items():
            setattr(variant, field, value)
        if actor_id is not None:
            variant.updated_by_id = actor_id

        self._session.flush()
        return variant

    def delete_variant(self, variant_id: UUIDv7, *, actor_id: UUIDv7 | None = None) -> None:
        """Soft-delete a catalog variant while preserving its business history."""
        variant = self.get_variant(variant_id)
        variant.soft_delete(actor_id)
        self._session.flush()

    def _ensure_product_is_active(self, product_id: UUIDv7 | None) -> None:
        """Raise when a variant product is unavailable for assignment."""
        if product_id is None:
            raise CatalogVariantProductError
        product = self._product_repository.get(product_id)
        if product is None or not product.is_active:
            raise CatalogVariantProductError

    def _register_barcode(
        self,
        variant: CatalogVariant,
        value: str,
        source: BarcodeSource,
        *,
        actor_id: UUIDv7 | None,
    ) -> CatalogVariantBarcode:
        normalized = normalize_barcode(value)
        existing = self._barcode_repository.get_by_value(normalized)
        if existing is not None:
            if existing.variant_id == variant.id and existing.source == source:
                return existing
            raise CatalogVariantBarcodeConflictError
        barcode = CatalogVariantBarcode(
            variant_id=variant.id,
            value=normalized,
            source=source,
            created_by_id=actor_id,
        )
        self._barcode_repository.add(barcode)
        variant.barcodes.append(barcode)
        return barcode


class CatalogRestoreService:
    """Restore archived Catalog entities after locking and revalidating live invariants."""

    def __init__(self, session: Session) -> None:
        """Bind restore validation to the route-owned transaction."""
        self._session = session
        self._categories = CategoryRepository(session)
        self._products = CatalogProductRepository(session)
        self._variants = CatalogVariantRepository(session)

    def restore_product(
        self,
        product_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CatalogProduct:
        """Restore only the Product while preserving every child Variant lifecycle state."""
        product = self._products.get_any_for_update(product_id)
        if product is None:
            raise CatalogRestoreNotFoundError
        if product.deleted_at is None:
            return product
        category = self._categories.get_any_for_update(product.category_id)
        if category is None or category.deleted_at is not None or not category.is_active:
            raise CatalogProductRestoreCategoryError(category.title if category else None)
        slug_owner = self._products.get_by_slug_for_update(product.slug)
        if slug_owner is not None and slug_owner.id != product.id:
            raise CatalogProductRestoreSlugConflictError
        product.restore()
        if actor_id is not None:
            product.updated_by_id = actor_id
        self._session.flush()
        return product

    def restore_variant(
        self,
        variant_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CatalogVariant:
        """Restore one Variant only under an active Product with unchanged identifiers."""
        variant = self._variants.get_any_for_update(variant_id)
        if variant is None:
            raise CatalogRestoreNotFoundError
        if variant.deleted_at is None:
            return variant
        product = self._products.get_any_for_update(variant.product_id)
        if product is None or product.deleted_at is not None or not product.is_active:
            raise CatalogVariantRestoreProductError
        sku_owner = self._variants.get_active_by_sku(variant.sku)
        if sku_owner is not None and sku_owner.id != variant.id:
            raise CatalogVariantRestoreSkuConflictError(variant.sku)
        barcode_owner = self._variants.get_active_by_barcode(variant.barcode)
        if barcode_owner is not None and barcode_owner.id != variant.id:
            raise CatalogVariantRestoreBarcodeConflictError(variant.barcode)
        barcode_history_owner = self._session.scalar(
            select(CatalogVariantBarcode)
            .join(CatalogVariant, CatalogVariant.id == CatalogVariantBarcode.variant_id)
            .where(
                CatalogVariantBarcode.value == variant.barcode,
                CatalogVariantBarcode.variant_id != variant.id,
                CatalogVariantBarcode.deleted_at.is_(None),
                CatalogVariant.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if barcode_history_owner is not None:
            raise CatalogVariantRestoreBarcodeConflictError(variant.barcode)
        variant.restore()
        if actor_id is not None:
            variant.updated_by_id = actor_id
        self._session.flush()
        return variant
