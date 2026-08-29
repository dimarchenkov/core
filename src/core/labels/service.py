from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant
from core.catalog.repository import CatalogVariantRepository
from core.labels.renderer import (
    LabelProfile,
    RentalAssetLabelData,
    RentalAssetLabelRenderer,
    VariantLabelData,
    VariantLabelRenderer,
)
from core.pricing.enums import PriceType
from core.pricing.repository import PriceRepository
from core.readiness.enums import ReadyForSaleRequirement
from core.readiness.service import ReadinessVariantNotFoundError, ReadyForSaleService
from core.rental.enums import AssetPurpose, RentalAvailability
from core.rental.models import RentalAssetRecord, RentalOrderItemRecord
from core.rental.order_enums import RentalOrderItemStatus
from core.shared.db import UUIDv7


class LabelVariantNotFoundError(Exception):
    """Raised when the requested Variant is missing or archived."""


class LabelVariantNotReadyError(Exception):
    """Raised when a sale label is requested before required work is complete."""

    def __init__(self, missing_requirements: list[ReadyForSaleRequirement]) -> None:
        """Preserve actionable readiness reasons for the API response."""
        self.missing_requirements = missing_requirements
        super().__init__("Variant is not ready for sale.")


class LabelRentalAssetNotFoundError(Exception):
    """Raised when a RentalAsset does not exist for inventory-label printing."""


class VariantLabelService:
    """Build printable product labels from authoritative Core data."""

    def __init__(
        self,
        session: Session,
        renderer: VariantLabelRenderer | None = None,
    ) -> None:
        """Create a label service with repositories and a PDF renderer."""
        self._variant_repository = CatalogVariantRepository(session)
        self._price_repository = PriceRepository(session)
        self._readiness_service = ReadyForSaleService(session)
        self._renderer = renderer or VariantLabelRenderer()

    def generate(
        self,
        variant_id: UUIDv7,
        profile: LabelProfile,
        *,
        dpi: int = 203,
        at: datetime | None = None,
    ) -> bytes:
        """Generate one fixed-profile sale label from authoritative current data."""
        return self._generate(variant_id, profile=profile, dpi=dpi, at=at)

    def generate_58x40(self, variant_id: UUIDv7, *, at: datetime | None = None) -> bytes:
        """Generate one 58 x 40 mm sale label for a currently ready Variant."""
        return self._generate(
            variant_id,
            profile=LabelProfile.STANDARD_58X40,
            dpi=203,
            at=at,
        )

    def _generate(
        self,
        variant_id: UUIDv7,
        *,
        profile: LabelProfile,
        dpi: int,
        at: datetime | None,
    ) -> bytes:
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
            ),
            profile=profile,
            dpi=dpi,
        )


class RentalAssetLabelService:
    """Build inventory labels without consulting Pricing, readiness, or AQSI."""

    def __init__(
        self,
        session: Session,
        renderer: RentalAssetLabelRenderer | None = None,
    ) -> None:
        """Create an asset-label service with an injectable PDF renderer."""
        self._session = session
        self._renderer = renderer or RentalAssetLabelRenderer()

    def generate(
        self,
        asset_id: UUIDv7,
        profile: LabelProfile,
        *,
        dpi: int = 203,
    ) -> bytes:
        """Generate a stable Code 128 label for an existing physical asset."""
        row = self._session.execute(
            select(RentalAssetRecord, CatalogVariant, CatalogProduct)
            .join(CatalogVariant, CatalogVariant.id == RentalAssetRecord.variant_id)
            .join(CatalogProduct, CatalogProduct.id == CatalogVariant.product_id)
            .where(
                RentalAssetRecord.id == asset_id,
                RentalAssetRecord.deleted_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            raise LabelRentalAssetNotFoundError
        asset, variant, product = row
        lost = self._session.scalar(
            select(RentalOrderItemRecord.id).where(
                RentalOrderItemRecord.rental_asset_id == asset.id,
                RentalOrderItemRecord.status == RentalOrderItemStatus.LOST,
            ).limit(1)
        ) is not None
        return self._renderer.render(
            RentalAssetLabelData(
                product_title=product.title,
                variant_title=variant.title,
                asset_number=asset.asset_number,
                sku=variant.sku,
                status_text=self._status_text(asset.purpose, asset.availability, lost=lost),
            ),
            profile=profile,
            dpi=dpi,
        )

    @staticmethod
    def _status_text(
        purpose: AssetPurpose,
        availability: RentalAvailability,
        *,
        lost: bool,
    ) -> str:
        if lost:
            return "Потерян"
        if purpose is AssetPurpose.SALE:
            return "Выведен из аренды"
        if purpose is AssetPurpose.RETIRED:
            return "Списан"
        return {
            RentalAvailability.AVAILABLE: "Доступен",
            RentalAvailability.RENTED: "Выдан",
            RentalAvailability.MAINTENANCE: "На обслуживании",
        }[availability]
