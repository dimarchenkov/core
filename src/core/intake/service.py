from __future__ import annotations

from sqlalchemy.orm import Session

from core.catalog.schemas import CatalogProductCreate, CatalogVariantCreate
from core.catalog.service import CatalogProductService, CatalogVariantService
from core.intake.schemas import IntakeCreate, IntakeRead
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.schemas import ImageLinkCreate
from core.media.service import ImageLinkService
from core.shared.db import UUIDv7, generate_uuid_v7


class IntakeService:
    """Orchestrate creation of the first product, variant, and primary image link."""

    def __init__(self, session: Session) -> None:
        """Create an intake service using the caller's database session."""
        self._session = session
        self._product_service = CatalogProductService(session)
        self._variant_service = CatalogVariantService(session)
        self._image_link_service = ImageLinkService(session)

    def create_intake(self, data: IntakeCreate, *, actor_id: UUIDv7 | None = None) -> IntakeRead:
        """Create an intake atomically by coordinating existing domain services."""
        try:
            product = self._product_service.create_product(
                CatalogProductCreate(
                    title=data.product_title,
                    slug=self._build_product_slug(),
                    description=data.product_description,
                    category_id=data.category_id,
                ),
                commit=False,
                actor_id=actor_id,
            )
            variant = self._variant_service.create_variant(
                CatalogVariantCreate(
                    product_id=product.id,
                    title=data.variant_title,
                    attributes=data.attributes,
                ),
                commit=False,
                actor_id=actor_id,
            )
            image_link = self._image_link_service.create_link(
                ImageLinkCreate(
                    image_id=data.image_id,
                    entity_type=ImageLinkEntityType.CATALOG_VARIANT,
                    entity_id=variant.id,
                    role=ImageLinkRole.PRIMARY,
                ),
                commit=False,
                actor_id=actor_id,
            )
            self._session.commit()
            self._session.refresh(product)
            self._session.refresh(variant)
            self._session.refresh(image_link)
        except Exception:
            self._session.rollback()
            raise

        return IntakeRead(
            product_id=product.id,
            variant_id=variant.id,
            sku=variant.sku,
            image_link_id=image_link.id,
        )

    def _build_product_slug(self) -> str:
        """Generate a unique internal slug because the intake request has no slug field."""
        return f"product-{generate_uuid_v7()}"
