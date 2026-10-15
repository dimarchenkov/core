from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator

from core.catalog.collection_enums import (
    CollectionRuleField,
    CollectionRuleOperator,
    CollectionType,
)
from core.shared.db import UUIDv7


class CollectionRuleWrite(PydanticBaseModel):
    """One operator-entered condition whose value is normalized by field type."""

    model_config = ConfigDict(extra="forbid")

    field: CollectionRuleField
    operator: CollectionRuleOperator
    value: str | Decimal | int | bool | UUIDv7


class CollectionCreate(PydanticBaseModel):
    """Create a stable Collection and optional initial dynamic rule set."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    type: CollectionType
    is_active: bool = True
    description: str | None = None
    sort_order: int = 0
    rules: list[CollectionRuleWrite] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        """Trim the required operator-visible name before validation."""
        return value.strip() if isinstance(value, str) else value

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_optional_slug(cls, value: object) -> object:
        """Keep an optional technical slug normalized for API clients."""
        normalized = value.strip() if isinstance(value, str) else value
        return normalized or None

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        """Store an absent description as NULL instead of blank whitespace."""
        normalized = value.strip() if value is not None else None
        return normalized or None


class CollectionUpdate(PydanticBaseModel):
    """Mutable Collection metadata; type remains stable after creation."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    type: CollectionType | None = None
    is_active: bool | None = None
    description: str | None = None
    sort_order: int | None = None

    @field_validator("name", "slug", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        """Trim changed Collection identity fields."""
        return value.strip() if isinstance(value, str) else value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        """Normalize an explicitly changed description."""
        normalized = value.strip() if value is not None else None
        return normalized or None


class CollectionRulesUpdate(PydanticBaseModel):
    """Replace the complete AND-rule set of one DYNAMIC Collection."""

    model_config = ConfigDict(extra="forbid")

    rules: list[CollectionRuleWrite] = Field(min_length=1)


class CollectionMembershipAdd(PydanticBaseModel):
    """Add one or more concrete Variants to a STATIC Collection."""

    model_config = ConfigDict(extra="forbid")

    variant_ids: list[UUIDv7] = Field(min_length=1)


class VariantStaticCollectionsUpdate(PydanticBaseModel):
    """Replace one Variant's memberships among active STATIC Collections."""

    model_config = ConfigDict(extra="forbid")

    collection_ids: list[UUIDv7] = Field(default_factory=list)


class CollectionRuleRead(PydanticBaseModel):
    """Typed dynamic rule returned to API clients."""

    id: UUIDv7
    field: CollectionRuleField
    operator: CollectionRuleOperator
    value: str | Decimal | int | bool | UUIDv7


class CollectionSummaryRead(PydanticBaseModel):
    """Collection row with a live Variant count."""

    id: UUIDv7
    name: str
    slug: str
    type: CollectionType
    is_active: bool
    description: str | None
    sort_order: int
    variant_count: int
    created_at: datetime
    updated_at: datetime


class CollectionDetailRead(CollectionSummaryRead):
    """Collection metadata plus its DYNAMIC conditions when applicable."""

    rules: list[CollectionRuleRead]


class CollectionVariantRead(PydanticBaseModel):
    """Concrete Variant projection shared by members, previews, and search."""

    id: UUIDv7
    product_id: UUIDv7
    product_name: str
    variant_name: str
    sku: str
    current_retail_price: Decimal | None
    stock: Decimal
    is_active: bool


class CollectionPreviewRead(PydanticBaseModel):
    """Bounded dynamic preview with an exact live match count."""

    count: int
    items: list[CollectionVariantRead]


class VariantCollectionOptionRead(PydanticBaseModel):
    """Collection membership state shown inside one Variant card."""

    id: UUIDv7
    name: str
    is_member: bool


class VariantCollectionsRead(PydanticBaseModel):
    """Editable STATIC memberships and computed DYNAMIC matches for one Variant."""

    static_collections: list[VariantCollectionOptionRead]
    dynamic_collections: list[CollectionSummaryRead]
