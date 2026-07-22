from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.media.enums import ImageLinkEntityType
from core.media.repository import ImageLinkRepository
from core.pricing.enums import PriceType
from core.pricing.repository import PriceRepository
from core.readiness.policy import ReadyForSaleFacts, derive_ready_for_sale_requirements
from core.readiness.schemas import ReadyForSaleRead
from core.shared.db import UUIDv7


class ReadinessVariantNotFoundError(Exception):
    """Raised when a variant is missing or archived."""


class ReadyForSaleService:
    """Derive sellability from current catalog, media, and pricing facts."""

    def __init__(self, session: Session) -> None:
        """Create a readiness service using the current database session."""
        self._variant_repository = CatalogVariantRepository(session)
        self._image_link_repository = ImageLinkRepository(session)
        self._price_repository = PriceRepository(session)

    def check_variant(
        self,
        variant_id: UUIDv7,
        *,
        at: datetime | None = None,
    ) -> ReadyForSaleRead:
        """Return current readiness and every missing requirement in stable order."""
        variant = self._variant_repository.get(variant_id)
        if variant is None:
            raise ReadinessVariantNotFoundError

        has_primary_image = self._image_link_repository.has_active_primary_for_entity(
            ImageLinkEntityType.CATALOG_VARIANT,
            variant.id,
        )
        retail_price = self._price_repository.get_current(
            variant.id,
            PriceType.RETAIL,
            at=at or datetime.now(UTC),
        )
        missing = derive_ready_for_sale_requirements(
            ReadyForSaleFacts(
                is_active=variant.is_active,
                has_primary_image=has_primary_image,
                sku=variant.sku,
                barcode=variant.barcode,
                retail_amount=retail_price.amount if retail_price is not None else None,
                retail_currency=retail_price.currency if retail_price is not None else None,
            )
        )

        return ReadyForSaleRead(
            variant_id=variant.id,
            is_ready=not missing,
            missing_requirements=missing,
        )
