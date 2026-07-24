from core.rental.asset import RentalAsset
from core.rental.models import RentalAssetRecord
from core.shared.db import UUIDv7


def rental_asset_to_record(
    asset: RentalAsset,
    *,
    actor_id: UUIDv7 | None = None,
) -> RentalAssetRecord:
    """Map a domain aggregate into its persistence projection."""
    return RentalAssetRecord(
        id=asset.id,
        asset_number=asset.asset_number,
        variant_id=asset.variant_id,
        intake_item_id=asset.intake_item_id,
        purpose=asset.purpose,
        condition=asset.condition,
        availability=asset.availability,
        retirement_reason=asset.retirement_reason,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        created_by_id=actor_id,
    )


def rental_asset_from_record(record: RentalAssetRecord) -> RentalAsset:
    """Rehydrate a domain aggregate from its persisted current-state projection."""
    return RentalAsset(
        record.id,
        record.asset_number,
        record.variant_id,
        record.intake_item_id,
        record.purpose,
        record.condition,
        record.availability,
        record.created_at,
        record.updated_at,
        record.retirement_reason,
    )
