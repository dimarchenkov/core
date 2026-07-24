from __future__ import annotations

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type) -> list[str]:
    """Return stable string values for database enum persistence."""
    return [member.value for member in enum_class]


class RentalAssetRecord(BaseModel):
    """Persisted state projection for one RentalAsset aggregate."""

    __tablename__ = "rental_assets"

    asset_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    variant_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    intake_item_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("intake_item_drafts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[AssetPurpose] = mapped_column(
        Enum(AssetPurpose, name="asset_purpose", values_callable=_enum_values),
        nullable=False,
    )
    condition: Mapped[AssetCondition] = mapped_column(
        Enum(AssetCondition, name="asset_condition", values_callable=_enum_values),
        nullable=False,
    )
    availability: Mapped[RentalAvailability] = mapped_column(
        Enum(
            RentalAvailability,
            name="rental_availability",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    retirement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because RentalAsset identity and history are permanent."""
        del actor_id
        raise RuntimeError("Rental assets cannot be deleted.")

    def restore(self) -> None:
        """Reject restoration because RentalAsset records cannot be deleted."""
        raise RuntimeError("Rental assets cannot be restored.")
