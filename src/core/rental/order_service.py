from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.customers.enums import CustomerStatus
from core.customers.repository import CustomerRepository
from core.rental.asset import RentalAsset
from core.rental.mapper import (
    rental_asset_from_record,
    rental_order_from_record,
    rental_order_to_record,
    update_rental_asset_record,
    update_rental_order_record,
)
from core.rental.models import RentalAssetRecord, RentalOrderRecord
from core.rental.order import RentalOrder
from core.rental.order_enums import RentalOrderStatus
from core.rental.order_exceptions import InvalidRentalMoneyError, RentalOrderItemNotFoundError
from core.rental.order_number import RentalOrderNumberGenerator
from core.rental.order_schemas import (
    RentalOrderCreate,
    RentalOrderItemCompletion,
    RentalOrderItemCreate,
    RentalOrderItemOutcome,
    RentalOrderItemReturn,
    RentalOrderItemsComplete,
    RentalOrderUpdate,
)
from core.rental.repository import RentalAssetRepository, RentalOrderRepository
from core.shared.db import UUIDv7, generate_uuid_v7


class RentalOrderNotFoundError(Exception):
    """Raised when a rental order projection does not exist."""


class RentalCustomerUnavailableError(Exception):
    """Raised when a customer cannot enter a new rental order."""


class RentalAssetNotFoundError(Exception):
    """Raised when an order references a missing rental asset."""


class RentalAssetVariantNotFoundError(Exception):
    """Raised when an asset snapshot cannot resolve its catalog variant."""


class RentalOrderService:
    """Application workflows coordinating RentalOrder and related aggregates."""

    def __init__(self, session: Session) -> None:
        """Create a service using one transaction-capable database session."""
        self._session = session
        self._orders = RentalOrderRepository(session)
        self._customers = CustomerRepository(session)
        self._assets = RentalAssetRepository(session)
        self._variants = CatalogVariantRepository(session)

    def create_draft(
        self,
        data: RentalOrderCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Open an empty draft and capture current customer snapshots."""
        customer = self._customers.get(data.customer_id)
        if customer is None or customer.status is not CustomerStatus.ACTIVE:
            raise RentalCustomerUnavailableError
        order = RentalOrder.create(
            order_id=generate_uuid_v7(),
            order_number=RentalOrderNumberGenerator.generate(
                self._orders.reserve_next_order_number()
            ),
            customer_id=customer.id,
            customer_name_snapshot=customer.full_name,
            customer_phone_snapshot=customer.phone,
            planned_start_at=data.planned_start_at,
            planned_return_at=data.planned_return_at,
            deposit_amount=data.deposit_amount,
            note=data.note,
        )
        self._orders.add(rental_order_to_record(order, actor_id=actor_id))
        self._commit()
        return order

    def get(self, order_id: UUIDv7) -> RentalOrder:
        """Return one fully rehydrated rental order aggregate."""
        record = self._orders.get(order_id)
        if record is None:
            raise RentalOrderNotFoundError
        return rental_order_from_record(record)

    def search(
        self,
        query: str | None = None,
        *,
        status: RentalOrderStatus | None = None,
        customer_id: UUIDv7 | None = None,
    ) -> Sequence[RentalOrder]:
        """Search rental orders without exposing persistence projections."""
        return [
            rental_order_from_record(record)
            for record in self._orders.search(query, status=status, customer_id=customer_id)
        ]

    def update_draft(
        self,
        order_id: UUIDv7,
        data: RentalOrderUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Change approved mutable details through aggregate commands."""
        record, order = self._load_order(order_id, for_update=True)
        changes = data.model_dump(exclude_unset=True)
        if "planned_start_at" in changes or "planned_return_at" in changes:
            order.change_period(
                data.planned_start_at or order.planned_start_at,
                data.planned_return_at or order.planned_return_at,
            )
        if "deposit_amount" in changes:
            if data.deposit_amount is None:
                raise InvalidRentalMoneyError
            order.set_deposit(data.deposit_amount)
        if "note" in changes:
            order.update_note(data.note)
        update_rental_order_record(record, order, actor_id=actor_id)
        self._orders.save(record)
        self._commit()
        return order

    def add_item(
        self,
        order_id: UUIDv7,
        data: RentalOrderItemCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Add one existing physical asset and capture its current snapshots."""
        record, order = self._load_order(order_id, for_update=True)
        asset_record = self._assets.get(data.rental_asset_id)
        if asset_record is None:
            raise RentalAssetNotFoundError
        asset = rental_asset_from_record(asset_record)
        # Reuse the aggregate's checkout invariant as a side-effect-free
        # availability check. The mutated in-memory copy is intentionally not saved.
        asset.checkout()
        variant = self._variants.get(asset.variant_id)
        if variant is None:
            raise RentalAssetVariantNotFoundError
        order.add_item(
            item_id=generate_uuid_v7(),
            rental_asset_id=asset.id,
            asset_number_snapshot=asset.asset_number,
            title_snapshot=variant.title,
            agreed_price=data.agreed_price,
            discount=data.discount,
        )
        update_rental_order_record(record, order, actor_id=actor_id)
        self._orders.save(record)
        self._commit()
        return order

    def remove_item(
        self,
        order_id: UUIDv7,
        item_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Remove one prepared item from a draft aggregate."""
        record, order = self._load_order(order_id, for_update=True)
        order.remove_item(item_id)
        update_rental_order_record(record, order, actor_id=actor_id)
        self._orders.save(record)
        self._commit()
        return order

    def issue(
        self,
        order_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Atomically issue an order and all of its physical RentalAssets."""
        try:
            record, order = self._load_order(order_id, for_update=True)
            asset_pairs: list[tuple[RentalAssetRecord, RentalAsset]] = []
            for item in order.items:
                asset_record = self._assets.get_for_update(item.rental_asset_id)
                if asset_record is None:
                    raise RentalAssetNotFoundError
                asset_pairs.append((asset_record, rental_asset_from_record(asset_record)))
            for _, asset in asset_pairs:
                asset.checkout()
            order.issue()
            for asset_record, asset in asset_pairs:
                update_rental_asset_record(asset_record, asset)
                asset_record.updated_by_id = actor_id
            update_rental_order_record(record, order, actor_id=actor_id)
            record.issued_by_id = actor_id
            self._orders.save(record)
            self._commit()
            return order
        except Exception:
            self._session.rollback()
            raise

    def cancel(
        self,
        order_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Cancel an unissued order without changing RentalAssets."""
        record, order = self._load_order(order_id, for_update=True)
        order.cancel()
        update_rental_order_record(record, order, actor_id=actor_id)
        self._orders.save(record)
        self._commit()
        return order

    def return_item(
        self,
        order_id: UUIDv7,
        item_id: UUIDv7,
        data: RentalOrderItemReturn,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Atomically return one order item and its physical RentalAsset."""
        return self.complete_items(
            order_id,
            RentalOrderItemsComplete(
                items=[
                    RentalOrderItemCompletion(
                        item_id=item_id,
                        outcome=RentalOrderItemOutcome.RETURNED,
                        condition=data.condition,
                        charged_amount=data.charged_amount,
                        completed_at=data.returned_at,
                        note=data.return_note,
                    )
                ]
            ),
            actor_id=actor_id,
        )

    def complete_items(
        self,
        order_id: UUIDv7,
        data: RentalOrderItemsComplete,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Complete returned and lost items together in one transaction."""
        try:
            record, order = self._load_order(order_id, for_update=True)
            order_items = {item.id: item for item in order.items}
            returned_assets: list[tuple[RentalAssetRecord, RentalAsset]] = []
            completed_item_ids: set[UUIDv7] = set()
            for completion in data.items:
                item = order_items.get(completion.item_id)
                if item is None:
                    raise RentalOrderItemNotFoundError
                if completion.outcome is RentalOrderItemOutcome.RETURNED:
                    asset_record = self._assets.get_for_update(item.rental_asset_id)
                    if asset_record is None:
                        raise RentalAssetNotFoundError
                    asset = rental_asset_from_record(asset_record)
                    if completion.condition is None:
                        raise ValueError("Returned item condition is required.")
                    asset.accept_return(completion.condition)
                    order.return_item(
                        completion.item_id,
                        charged_amount=completion.charged_amount,
                        returned_at=completion.completed_at,
                        return_note=completion.note,
                    )
                    returned_assets.append((asset_record, asset))
                else:
                    order.mark_item_lost(
                        completion.item_id,
                        charged_amount=completion.charged_amount,
                        completed_at=completion.completed_at,
                        note=completion.note,
                    )
                completed_item_ids.add(completion.item_id)

            for asset_record, asset in returned_assets:
                update_rental_asset_record(asset_record, asset)
                asset_record.updated_by_id = actor_id
            update_rental_order_record(record, order, actor_id=actor_id)
            for item_record in record.items:
                if item_record.id in completed_item_ids:
                    item_record.completed_by_id = actor_id
            self._orders.save(record)
            self._commit()
            return order
        except Exception:
            self._session.rollback()
            raise

    def _load_order(
        self,
        order_id: UUIDv7,
        *,
        for_update: bool,
    ) -> tuple[RentalOrderRecord, RentalOrder]:
        """Load one persistence projection and its domain aggregate."""
        record = self._orders.get(order_id, for_update=for_update)
        if record is None:
            raise RentalOrderNotFoundError
        return record, rental_order_from_record(record)

    def _commit(self) -> None:
        """Commit one completed workflow and restore session usability on failure."""
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
