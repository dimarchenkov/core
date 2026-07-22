from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.intake.enums import (
    IntakeItemKind,
    IntakeItemRequirement,
    IntakeSessionRequirement,
)
from core.intake.models import IntakeItemDraft


@dataclass(frozen=True, slots=True)
class IntakeItemAvailability:
    """Current availability of catalog and media facts referenced by one draft."""

    variant: bool = False
    image: bool = False
    product: bool = False
    category: bool = False


def derive_item_requirements(
    item: IntakeItemDraft,
    availability: IntakeItemAvailability,
) -> list[IntakeItemRequirement]:
    """Return stable kind-specific requirements without performing persistence reads."""
    missing: list[IntakeItemRequirement] = []
    if item.kind is IntakeItemKind.EXISTING_VARIANT:
        if not availability.variant:
            missing.append(IntakeItemRequirement.MISSING_VARIANT)
    elif not availability.image:
        missing.append(IntakeItemRequirement.MISSING_IMAGE)

    if item.kind is IntakeItemKind.NEW_VARIANT:
        if not availability.product:
            missing.append(IntakeItemRequirement.MISSING_PRODUCT)
    elif item.kind is IntakeItemKind.NEW_PRODUCT:
        if not availability.category:
            missing.append(IntakeItemRequirement.MISSING_CATEGORY)
        if not (item.product_title or "").strip():
            missing.append(IntakeItemRequirement.MISSING_PRODUCT_TITLE)

    if (
        item.kind in {IntakeItemKind.NEW_PRODUCT, IntakeItemKind.NEW_VARIANT}
        and not (item.variant_title or "").strip()
    ):
        missing.append(IntakeItemRequirement.MISSING_VARIANT_TITLE)
    if item.quantity is None:
        missing.append(IntakeItemRequirement.MISSING_QUANTITY)
    if item.purchase_price is None:
        missing.append(IntakeItemRequirement.MISSING_PURCHASE_PRICE)
    return missing


def derive_session_requirements(
    *,
    has_supplier: bool,
    active_item_requirements: Sequence[Sequence[IntakeItemRequirement]],
) -> list[IntakeSessionRequirement]:
    """Derive session completeness from current item projections."""
    missing: list[IntakeSessionRequirement] = []
    if not has_supplier:
        missing.append(IntakeSessionRequirement.MISSING_SUPPLIER)
    if not active_item_requirements:
        missing.append(IntakeSessionRequirement.MISSING_ITEMS)
    elif any(requirements for requirements in active_item_requirements):
        missing.append(IntakeSessionRequirement.INCOMPLETE_ITEMS)
    return missing
