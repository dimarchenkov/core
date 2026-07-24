from __future__ import annotations

from sqlalchemy.orm import Session

from core.rental.asset import RentalAsset
from core.rental.asset_number import RentalAssetNumberGenerator
from core.rental.enums import AssetCondition
from core.rental.mapper import rental_asset_to_record
from core.rental.repository import RentalAssetRepository
from core.shared.db import UUIDv7, generate_uuid_v7


class RentalAssetService:
    """Stage RentalAsset aggregates inside a caller-owned business transaction."""

    def __init__(self, session: Session) -> None:
        """Create a service without taking ownership of commit or rollback."""
        self._session = session
        self._repository = RentalAssetRepository(session)

    def create_from_intake(
        self,
        *,
        variant_id: UUIDv7,
        intake_item_id: UUIDv7,
        quantity: int,
        actor_id: UUIDv7 | None = None,
    ) -> list[RentalAsset]:
        """Create one independently tracked new asset for every allocated rental unit."""
        assets: list[RentalAsset] = []
        for _ in range(quantity):
            number = self._repository.next_asset_number()
            asset = RentalAsset.create(
                asset_id=generate_uuid_v7(),
                asset_number=RentalAssetNumberGenerator.generate(number),
                variant_id=variant_id,
                intake_item_id=intake_item_id,
                condition=AssetCondition.NEW,
            )
            self._repository.add(rental_asset_to_record(asset, actor_id=actor_id))
            assets.append(asset)
            self._session.flush()
        return assets
