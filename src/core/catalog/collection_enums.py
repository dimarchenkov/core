from __future__ import annotations

from enum import StrEnum


class CollectionType(StrEnum):
    """Supported Catalog Collection membership strategies."""

    STATIC = "static"
    DYNAMIC = "dynamic"


class CollectionRuleField(StrEnum):
    """Whitelisted live Variant facts available to dynamic rules."""

    PRICE = "price"
    CATEGORY = "category"
    STOCK = "stock"
    IS_ACTIVE = "is_active"


class CollectionRuleOperator(StrEnum):
    """Small comparison vocabulary used by all dynamic Collection rules."""

    EQ = "eq"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
