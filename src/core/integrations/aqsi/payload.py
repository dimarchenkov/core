from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.config import Settings
from core.integrations.aqsi.schemas import AqsiDefaultCategoryPayload, AqsiGoodsPayload
from core.pricing.enums import PriceType
from core.pricing.repository import PriceRepository
from core.readiness.service import ReadinessVariantNotFoundError, ReadyForSaleService
from core.shared.db import UUIDv7


class AqsiVariantNotFoundError(Exception):
    """Raised when a publication target no longer exists."""


class AqsiVariantNotReadyError(Exception):
    """Raised when a Variant does not satisfy Core Ready for Sale."""

    def __init__(self, missing_requirements: list[str]) -> None:
        """Preserve machine-readable readiness failures."""
        self.missing_requirements = missing_requirements
        super().__init__(", ".join(missing_requirements))


class AqsiPayloadBuilder:
    """Build deterministic AQSI requests from authoritative Core data."""

    def __init__(self, session: Session, settings: Settings) -> None:
        """Create a mapper using current Core data and AQSI configuration."""
        self._variant_repository = CatalogVariantRepository(session)
        self._price_repository = PriceRepository(session)
        self._readiness_service = ReadyForSaleService(session)
        self._settings = settings

    def build_goods(self, variant_id: UUIDv7) -> AqsiGoodsPayload:
        """Return the minimal ordinary-goods payload for a ready Variant."""
        try:
            readiness = self._readiness_service.check_variant(variant_id)
        except ReadinessVariantNotFoundError as exc:
            raise AqsiVariantNotFoundError from exc
        if not readiness.is_ready:
            raise AqsiVariantNotReadyError(
                [requirement.value for requirement in readiness.missing_requirements]
            )

        variant = self._variant_repository.get(variant_id)
        if variant is None:
            raise AqsiVariantNotFoundError
        retail_price = self._price_repository.get_current(
            variant.id,
            PriceType.RETAIL,
            at=datetime.now(UTC),
        )
        if retail_price is None:
            raise AqsiVariantNotReadyError(["missing_retail_price"])

        return AqsiGoodsPayload(
            id=str(variant.id),
            group_id=self._settings.aqsi_default_group_id,
            name=self._display_name(variant.product.title, variant.title),
            tax=self._settings.aqsi_tax_code,
            sku=variant.sku,
            price=float(retail_price.amount),
            barcodes=[variant.barcode],
        )

    def build_default_category(self) -> AqsiDefaultCategoryPayload:
        """Return the deterministic default AQSI category payload."""
        return AqsiDefaultCategoryPayload(
            id=self._settings.aqsi_default_group_id,
            name=self._settings.aqsi_default_group_name,
            defaultTax=self._settings.aqsi_tax_code,
        )

    @staticmethod
    def canonical_hash(payload: AqsiGoodsPayload) -> str:
        """Hash canonical AQSI JSON for idempotency and drift detection."""
        encoded = json.dumps(
            payload.as_aqsi_json(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _display_name(product_title: str, variant_title: str) -> str:
        """Compose a compact AQSI name within its 128-character limit."""
        product = product_title.strip()
        variant = variant_title.strip()
        if not variant or variant.casefold() == product.casefold():
            name = product
        else:
            name = f"{product}, {variant}"
        return name[:128].rstrip()
