from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.pricing.repository import PriceRepository
from core.pricing.schemas import PriceCreate
from core.shared.db import UUIDv7
from core.shared.money import DEFAULT_CURRENCY, quantize_money


class PriceVariantNotFoundError(Exception):
    """Raised when the target variant is missing, archived, or inactive."""


class UnsupportedCurrencyError(Exception):
    """Raised when a price uses a currency not yet supported by Core."""


class CurrentPriceNotFoundError(Exception):
    """Raised when no price is effective for the requested variant and type."""


class PriceService:
    """Business operations for append-only sellable variant prices."""

    def __init__(self, session: Session) -> None:
        """Create a service using the given database session."""
        self._session = session
        self._repository = PriceRepository(session)
        self._variant_repository = CatalogVariantRepository(session)

    def set_price(
        self,
        variant_id: UUIDv7,
        data: PriceCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Price:
        """Stage a normalized price fact for the command owner to commit."""
        self._ensure_variant_is_active(variant_id)
        if data.currency != DEFAULT_CURRENCY:
            raise UnsupportedCurrencyError

        price = Price(
            variant_id=variant_id,
            price_type=data.price_type,
            amount=quantize_money(data.amount),
            currency=data.currency,
            effective_from=data.effective_from or datetime.now(UTC),
            reason=data.reason,
            created_by_id=actor_id,
        )
        self._repository.add(price)
        self._session.flush()
        return price

    def get_current_price(
        self,
        variant_id: UUIDv7,
        price_type: PriceType,
        *,
        at: datetime | None = None,
    ) -> Price:
        """Return the current price after validating the catalog variant."""
        self._ensure_variant_exists(variant_id)
        price = self._repository.get_current(
            variant_id,
            price_type,
            at=at or datetime.now(UTC),
        )
        if price is None:
            raise CurrentPriceNotFoundError
        return price

    def get_price_history(
        self,
        variant_id: UUIDv7,
        *,
        price_type: PriceType | None = None,
    ) -> Sequence[Price]:
        """Return immutable history for an existing catalog variant."""
        self._ensure_variant_exists(variant_id)
        return self._repository.list_history(variant_id, price_type=price_type)

    def _ensure_variant_exists(self, variant_id: UUIDv7) -> None:
        """Require a non-deleted variant for normal price reads."""
        if self._variant_repository.get(variant_id) is None:
            raise PriceVariantNotFoundError

    def _ensure_variant_is_active(self, variant_id: UUIDv7) -> None:
        """Require a non-deleted active variant before accepting a new price."""
        variant = self._variant_repository.get(variant_id)
        if variant is None or not variant.is_active:
            raise PriceVariantNotFoundError
