from enum import StrEnum


class IntakeSessionStatus(StrEnum):
    """Lifecycle states of a resumable intake workspace."""

    DRAFT = "draft"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class IntakeItemKind(StrEnum):
    """Supported identification paths for one intake item draft."""

    EXISTING_VARIANT = "existing_variant"
    NEW_VARIANT = "new_variant"
    NEW_PRODUCT = "new_product"


class IntakeItemRequirement(StrEnum):
    """Derived facts still required before an intake item can complete."""

    MISSING_IMAGE = "missing_image"
    MISSING_VARIANT = "missing_variant"
    MISSING_PRODUCT = "missing_product"
    MISSING_CATEGORY = "missing_category"
    MISSING_PRODUCT_TITLE = "missing_product_title"
    MISSING_VARIANT_TITLE = "missing_variant_title"
    MISSING_QUANTITY = "missing_quantity"
    MISSING_PURCHASE_PRICE = "missing_purchase_price"


class IntakeSessionRequirement(StrEnum):
    """Derived facts still required before an intake session can complete."""

    MISSING_SUPPLIER = "missing_supplier"
    MISSING_ITEMS = "missing_items"
    INCOMPLETE_ITEMS = "incomplete_items"
