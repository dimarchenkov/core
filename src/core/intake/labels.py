from __future__ import annotations

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogProductRepository, CatalogVariantRepository
from core.intake.enums import IntakeItemKind
from core.intake.repository import IntakeItemDraftRepository, IntakeSessionRepository
from core.labels.renderer import LabelProfile, VariantLabelData, VariantLabelRenderer
from core.shared.db import UUIDv7


class IntakeDraftLabelNotFoundError(Exception):
    """Raised when an owned draft Variant cannot provide stable label identity."""


class IntakeDraftLabelService:
    """Render a saved Intake Variant from its stable reserved Catalog identity."""

    def __init__(self, session: Session, renderer: VariantLabelRenderer | None = None) -> None:
        """Bind draft and Catalog projections to the shared label renderer."""
        self._sessions = IntakeSessionRepository(session)
        self._items = IntakeItemDraftRepository(session)
        self._products = CatalogProductRepository(session)
        self._variants = CatalogVariantRepository(session)
        self._renderer = renderer or VariantLabelRenderer()

    def generate(
        self,
        session_id: UUIDv7,
        item_id: UUIDv7,
        *,
        actor_id: UUIDv7,
        profile: LabelProfile,
        dpi: int = 203,
    ) -> bytes:
        """Generate labels for one owned, non-abandoned, saved draft Variant."""
        intake_session = self._sessions.get_owned(session_id, actor_id)
        item = self._items.get(session_id, item_id)
        if (
            intake_session is None
            or item is None
            or item.abandoned_at is not None
        ):
            raise IntakeDraftLabelNotFoundError
        if item.kind is IntakeItemKind.EXISTING_VARIANT:
            variant = self._variants.get(item.variant_id) if item.variant_id is not None else None
            if variant is None:
                raise IntakeDraftLabelNotFoundError
            product = self._products.get(variant.product_id)
            if product is None:
                raise IntakeDraftLabelNotFoundError
            return self._renderer.render(
                VariantLabelData(
                    product_title=product.title,
                    variant_details=variant.title,
                    price=item.retail_price,
                    barcode=variant.barcode,
                    sku=variant.sku,
                ),
                profile=profile,
                dpi=dpi,
            )
        if item.reserved_sku is None or item.reserved_internal_barcode is None:
            raise IntakeDraftLabelNotFoundError
        product_title = item.product_title
        if item.kind is IntakeItemKind.NEW_VARIANT:
            if item.product_id is not None:
                product = self._products.get(item.product_id)
                product_title = product.title if product is not None else None
            elif item.draft_product_item_id is not None:
                root = self._items.get(session_id, item.draft_product_item_id)
                product_title = root.product_title if root is not None else None
        if not (product_title or "").strip():
            raise ValueError("Save the Product name before printing labels.")
        return self._renderer.render(
            VariantLabelData(
                product_title=product_title or "",
                variant_details=item.variant_title or "Default",
                price=item.retail_price,
                barcode=item.manufacturer_barcode or item.reserved_internal_barcode,
                sku=item.reserved_sku,
            ),
            profile=profile,
            dpi=dpi,
        )
