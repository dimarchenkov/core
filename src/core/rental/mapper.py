from core.rental.asset import RentalAsset
from core.rental.models import RentalAssetRecord, RentalOrderItemRecord, RentalOrderRecord
from core.rental.order import RentalOrder, RentalOrderItem
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


def update_rental_asset_record(record: RentalAssetRecord, asset: RentalAsset) -> None:
    """Copy mutable RentalAsset state into its persisted projection."""
    record.purpose = asset.purpose
    record.condition = asset.condition
    record.availability = asset.availability
    record.retirement_reason = asset.retirement_reason
    record.updated_at = asset.updated_at


def rental_order_to_record(
    order: RentalOrder,
    *,
    actor_id: UUIDv7 | None = None,
) -> RentalOrderRecord:
    """Map a new RentalOrder aggregate and owned entities into persistence."""
    record = RentalOrderRecord(
        id=order.id,
        order_number=order.order_number,
        customer_id=order.customer_id,
        customer_name_snapshot=order.customer_name_snapshot,
        customer_phone_snapshot=order.customer_phone_snapshot,
        status=order.status,
        planned_start_at=order.planned_start_at,
        planned_return_at=order.planned_return_at,
        issued_at=order.issued_at,
        closed_at=order.closed_at,
        deposit_amount=order.deposit_amount,
        note=order.note,
        created_by_id=actor_id,
    )
    record.items = [
        _rental_order_item_to_record(item, order.id, actor_id=actor_id)
        for item in order.items
    ]
    return record


def rental_order_from_record(record: RentalOrderRecord) -> RentalOrder:
    """Fully rehydrate a RentalOrder aggregate and its owned entities."""
    return RentalOrder(
        id=record.id,
        order_number=record.order_number,
        customer_id=record.customer_id,
        customer_name_snapshot=record.customer_name_snapshot,
        customer_phone_snapshot=record.customer_phone_snapshot,
        status=record.status,
        planned_start_at=record.planned_start_at,
        planned_return_at=record.planned_return_at,
        issued_at=record.issued_at,
        closed_at=record.closed_at,
        deposit_amount=record.deposit_amount,
        note=record.note,
        items=[_rental_order_item_from_record(item) for item in record.items],
    )


def update_rental_order_record(
    record: RentalOrderRecord,
    order: RentalOrder,
    *,
    actor_id: UUIDv7 | None = None,
) -> None:
    """Synchronize an existing projection with aggregate-controlled state."""
    record.status = order.status
    record.planned_start_at = order.planned_start_at
    record.planned_return_at = order.planned_return_at
    record.issued_at = order.issued_at
    record.closed_at = order.closed_at
    record.deposit_amount = order.deposit_amount
    record.note = order.note
    record.updated_by_id = actor_id

    existing_by_id = {item.id: item for item in record.items}
    current_ids = {item.id for item in order.items}
    record.items[:] = [item for item in record.items if item.id in current_ids]
    for domain_item in order.items:
        item_record = existing_by_id.get(domain_item.id)
        if item_record is None:
            record.items.append(
                _rental_order_item_to_record(domain_item, order.id, actor_id=actor_id)
            )
            continue
        item_record.status = domain_item.status
        item_record.charged_amount = domain_item.charged_amount
        item_record.returned_at = domain_item.returned_at
        item_record.return_note = domain_item.return_note
        item_record.updated_by_id = actor_id


def _rental_order_item_to_record(
    item: RentalOrderItem,
    order_id: UUIDv7,
    *,
    actor_id: UUIDv7 | None,
) -> RentalOrderItemRecord:
    """Map one aggregate-owned item into its persistence projection."""
    return RentalOrderItemRecord(
        id=item.id,
        order_id=order_id,
        rental_asset_id=item.rental_asset_id,
        asset_number_snapshot=item.asset_number_snapshot,
        title_snapshot=item.title_snapshot,
        agreed_price=item.agreed_price,
        discount=item.discount,
        charged_amount=item.charged_amount,
        returned_at=item.returned_at,
        return_note=item.return_note,
        status=item.status,
        created_by_id=actor_id,
    )


def _rental_order_item_from_record(record: RentalOrderItemRecord) -> RentalOrderItem:
    """Rehydrate one aggregate-owned item without running creation commands."""
    return RentalOrderItem(
        id=record.id,
        rental_asset_id=record.rental_asset_id,
        asset_number_snapshot=record.asset_number_snapshot,
        title_snapshot=record.title_snapshot,
        agreed_price=record.agreed_price,
        discount=record.discount,
        charged_amount=record.charged_amount,
        returned_at=record.returned_at,
        return_note=record.return_note,
        status=record.status,
    )
