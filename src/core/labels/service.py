from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.labels.renderer import VariantLabel58x40Renderer, VariantLabelData
from core.pricing.enums import PriceType
from core.pricing.repository import PriceRepository
from core.readiness.enums import ReadyForSaleRequirement
from core.readiness.service import ReadinessVariantNotFoundError, ReadyForSaleService
from core.shared.db import UUIDv7


class LabelVariantNotFoundError(Exception):
    """Raised when the requested Variant is missing or archived."""


class LabelVariantNotReadyError(Exception):
    """Raised when a sale label is requested before required work is complete."""

    def __init__(self, missing_requirements: list[ReadyForSaleRequirement]) -> None:
        """Preserve actionable readiness reasons for the API response."""
        self.missing_requirements = missing_requirements
        super().__init__("Variant is not ready for sale.")


class VariantLabelService:
    """Build printable product labels from authoritative Core data."""

    def __init__(
        self,
        session: Session,
        renderer: VariantLabel58x40Renderer | None = None,
    ) -> None:
        """Create a label service with repositories and a PDF renderer."""
        self._variant_repository = CatalogVariantRepository(session)
        self._price_repository = PriceRepository(session)
        self._readiness_service = ReadyForSaleService(session)
        self._renderer = renderer or VariantLabel58x40Renderer()

    def generate_58x40(self, variant_id: UUIDv7, *, at: datetime | None = None) -> bytes:
        """Generate one 58 x 40 mm sale label for a currently ready Variant."""
        effective_at = at or datetime.now(UTC)
        try:
            readiness = self._readiness_service.check_variant(variant_id, at=effective_at)
        except ReadinessVariantNotFoundError as exc:
            raise LabelVariantNotFoundError from exc
        if not readiness.is_ready:
            raise LabelVariantNotReadyError(readiness.missing_requirements)

        variant = self._variant_repository.get(variant_id)
        price = self._price_repository.get_current(
            variant_id,
            PriceType.RETAIL,
            at=effective_at,
        )
        if variant is None or price is None:
            raise LabelVariantNotFoundError

        attribute_values = [str(value) for _, value in sorted(variant.attributes.items())]
        details = " - ".join([variant.title, *attribute_values])
        return self._renderer.render(
            VariantLabelData(
                product_title=variant.product.title,
                variant_details=details,
                price=price.amount,
                barcode=variant.barcode,
                sku=variant.sku,
            )
        )
