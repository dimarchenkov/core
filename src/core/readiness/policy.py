from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.readiness.enums import ReadyForSaleRequirement
from core.shared.money import DEFAULT_CURRENCY


@dataclass(frozen=True, slots=True)
class ReadyForSaleFacts:
    """Authoritative current facts needed to derive one Variant's readiness."""

    is_active: bool
    has_primary_image: bool
    sku: str
    barcode: str
    retail_amount: Decimal | None
    retail_currency: str | None


def derive_ready_for_sale_requirements(
    facts: ReadyForSaleFacts,
) -> list[ReadyForSaleRequirement]:
    """Return all missing sale requirements in the stable UI/API order."""
    missing: list[ReadyForSaleRequirement] = []
    if not facts.is_active:
        missing.append(ReadyForSaleRequirement.INACTIVE_VARIANT)
    if not facts.has_primary_image:
        missing.append(ReadyForSaleRequirement.MISSING_PRIMARY_IMAGE)
    if not facts.sku.strip():
        missing.append(ReadyForSaleRequirement.MISSING_SKU)
    if not facts.barcode.strip():
        missing.append(ReadyForSaleRequirement.MISSING_BARCODE)
    elif not is_aqsi_compatible_barcode(facts.barcode):
        missing.append(ReadyForSaleRequirement.INVALID_BARCODE)
    if (
        facts.retail_amount is None
        or facts.retail_currency != DEFAULT_CURRENCY
        or facts.retail_amount <= 0
    ):
        missing.append(ReadyForSaleRequirement.MISSING_RETAIL_PRICE)
    return missing


def is_aqsi_compatible_barcode(barcode: str) -> bool:
    """Return whether the primary barcode can be sent to AQSI V2."""
    return barcode.isdigit() and 4 <= len(barcode) <= 22
