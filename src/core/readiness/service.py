from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.media.enums import ImageLinkEntityType
from core.media.repository import ImageLinkRepository
from core.pricing.enums import PriceType
from core.pricing.repository import PriceRepository
from core.readiness.enums import ReadyForSaleRequirement
from core.readiness.schemas import ReadyForSaleRead
from core.shared.db import UUIDv7
from core.shared.money import DEFAULT_CURRENCY


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

        missing: list[ReadyForSaleRequirement] = []
        if not variant.is_active:
            missing.append(ReadyForSaleRequirement.INACTIVE_VARIANT)
        if not self._image_link_repository.has_active_primary_for_entity(
            ImageLinkEntityType.CATALOG_VARIANT,
            variant.id,
        ):
            missing.append(ReadyForSaleRequirement.MISSING_PRIMARY_IMAGE)
        if not variant.sku.strip():
            missing.append(ReadyForSaleRequirement.MISSING_SKU)
        if not variant.barcode.strip():
            missing.append(ReadyForSaleRequirement.MISSING_BARCODE)
        elif not self._is_aqsi_compatible_barcode(variant.barcode):
            missing.append(ReadyForSaleRequirement.INVALID_BARCODE)

        retail_price = self._price_repository.get_current(
            variant.id,
            PriceType.RETAIL,
            at=at or datetime.now(UTC),
        )
        if (
            retail_price is None
            or retail_price.currency != DEFAULT_CURRENCY
            or retail_price.amount <= 0
        ):
            missing.append(ReadyForSaleRequirement.MISSING_RETAIL_PRICE)

        return ReadyForSaleRead(
            variant_id=variant.id,
            is_ready=not missing,
            missing_requirements=missing,
        )

    @staticmethod
    def _is_aqsi_compatible_barcode(barcode: str) -> bool:
        """Return whether the primary barcode can be sent to AQSI V2."""
        return barcode.isdigit() and 4 <= len(barcode) <= 22
