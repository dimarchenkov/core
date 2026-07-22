from __future__ import annotations

from decimal import Decimal

from core.intake.completeness import (
    IntakeItemAvailability,
    derive_item_requirements,
    derive_session_requirements,
)
from core.intake.enums import (
    IntakeItemKind,
    IntakeItemRequirement,
    IntakeSessionRequirement,
)
from core.intake.models import IntakeItemDraft


def test_existing_variant_completeness_uses_resolved_availability() -> None:
    """A repeat delivery needs no photo when its Variant is still available."""
    item = IntakeItemDraft(
        kind=IntakeItemKind.EXISTING_VARIANT,
        quantity=3,
        purchase_price=Decimal("125.00"),
    )

    requirements = derive_item_requirements(
        item,
        IntakeItemAvailability(variant=True),
    )

    assert requirements == []


def test_new_product_completeness_reports_domain_facts_in_stable_order() -> None:
    """The pure policy explains every missing Photo First draft fact."""
    item = IntakeItemDraft(kind=IntakeItemKind.NEW_PRODUCT)

    requirements = derive_item_requirements(item, IntakeItemAvailability())

    assert requirements == [
        IntakeItemRequirement.MISSING_IMAGE,
        IntakeItemRequirement.MISSING_CATEGORY,
        IntakeItemRequirement.MISSING_PRODUCT_TITLE,
        IntakeItemRequirement.MISSING_VARIANT_TITLE,
        IntakeItemRequirement.MISSING_QUANTITY,
        IntakeItemRequirement.MISSING_PURCHASE_PRICE,
    ]


def test_session_completeness_is_derived_from_active_item_projections() -> None:
    """Session state remains computed rather than stored independently."""
    requirements = derive_session_requirements(
        has_supplier=False,
        active_item_requirements=[[IntakeItemRequirement.MISSING_QUANTITY]],
    )

    assert requirements == [
        IntakeSessionRequirement.MISSING_SUPPLIER,
        IntakeSessionRequirement.INCOMPLETE_ITEMS,
    ]


def test_session_without_active_items_reports_missing_items() -> None:
    """Abandoned or absent positions do not make a session completeable."""
    requirements = derive_session_requirements(
        has_supplier=True,
        active_item_requirements=[],
    )

    assert requirements == [IntakeSessionRequirement.MISSING_ITEMS]
