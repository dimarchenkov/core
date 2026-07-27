from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from core.rental.order_enums import RentalOrderItemStatus, RentalOrderStatus
from core.rental.order_exceptions import (
    DuplicateRentalAssetError,
    InvalidRentalMoneyError,
    InvalidRentalOrderDataError,
    InvalidRentalOrderItemStateError,
    InvalidRentalOrderStateError,
    InvalidRentalPeriodError,
    RentalOrderHasNoItemsError,
    RentalOrderItemNotFoundError,
)
from core.shared.db import UUIDv7
from core.shared.money import quantize_money

_COMPLETED_ITEM_STATUSES = frozenset(
    {
        RentalOrderItemStatus.RETURNED,
        RentalOrderItemStatus.LOST,
        RentalOrderItemStatus.CANCELLED,
    }
)


@dataclass(slots=True, init=False)
class RentalOrderItem:
    """One physical asset entity controlled exclusively by RentalOrder."""

    _id: UUIDv7
    _rental_asset_id: UUIDv7
    _asset_number_snapshot: str
    _title_snapshot: str
    _agreed_price: Decimal
    _discount: Decimal
    _charged_amount: Decimal | None
    _returned_at: datetime | None
    _return_note: str | None
    _status: RentalOrderItemStatus

    def __init__(
        self,
        *,
        id: UUIDv7,
        rental_asset_id: UUIDv7,
        asset_number_snapshot: str,
        title_snapshot: str,
        agreed_price: Decimal,
        discount: Decimal,
        charged_amount: Decimal | None = None,
        returned_at: datetime | None = None,
        return_note: str | None = None,
        status: RentalOrderItemStatus = RentalOrderItemStatus.PREPARED,
    ) -> None:
        """Initialize entity state for aggregate construction or rehydration."""
        self._id = id
        self._rental_asset_id = rental_asset_id
        self._asset_number_snapshot = asset_number_snapshot
        self._title_snapshot = title_snapshot
        self._agreed_price = agreed_price
        self._discount = discount
        self._charged_amount = charged_amount
        self._returned_at = returned_at
        self._return_note = return_note
        self._status = status

    @property
    def id(self) -> UUIDv7:
        """Return the stable entity identifier."""
        return self._id

    @property
    def rental_asset_id(self) -> UUIDv7:
        """Return the physical asset represented by this item."""
        return self._rental_asset_id

    @property
    def asset_number_snapshot(self) -> str:
        """Return the immutable asset number captured for this order."""
        return self._asset_number_snapshot

    @property
    def title_snapshot(self) -> str:
        """Return the immutable item title captured for this order."""
        return self._title_snapshot

    @property
    def agreed_price(self) -> Decimal:
        """Return the agreed item price before discount."""
        return self._agreed_price

    @property
    def discount(self) -> Decimal:
        """Return the item discount fixed in the order."""
        return self._discount

    @property
    def charged_amount(self) -> Decimal | None:
        """Return the actual amount fixed when the item is completed."""
        return self._charged_amount

    @property
    def returned_at(self) -> datetime | None:
        """Return when this item was returned or declared lost."""
        return self._returned_at

    @property
    def return_note(self) -> str | None:
        """Return the completion note."""
        return self._return_note

    @property
    def status(self) -> RentalOrderItemStatus:
        """Return the current item lifecycle state."""
        return self._status

    def _issue(self) -> None:
        """Transition a prepared item as part of aggregate issue."""
        if self._status is not RentalOrderItemStatus.PREPARED:
            raise InvalidRentalOrderItemStateError
        self._status = RentalOrderItemStatus.ISSUED

    def _return(
        self,
        *,
        charged_amount: Decimal,
        returned_at: datetime,
        return_note: str | None,
    ) -> None:
        """Complete an issued item as physically returned."""
        self._complete(
            status=RentalOrderItemStatus.RETURNED,
            charged_amount=charged_amount,
            returned_at=returned_at,
            return_note=return_note,
        )

    def _mark_lost(
        self,
        *,
        charged_amount: Decimal,
        completed_at: datetime,
        note: str | None,
    ) -> None:
        """Complete an issued item as lost without implying physical return."""
        self._complete(
            status=RentalOrderItemStatus.LOST,
            charged_amount=charged_amount,
            returned_at=completed_at,
            return_note=note,
        )

    def _cancel(self) -> None:
        """Cancel an unissued item inside a draft order."""
        if self._status is not RentalOrderItemStatus.PREPARED:
            raise InvalidRentalOrderItemStateError
        self._status = RentalOrderItemStatus.CANCELLED

    def _complete(
        self,
        *,
        status: RentalOrderItemStatus,
        charged_amount: Decimal,
        returned_at: datetime,
        return_note: str | None,
    ) -> None:
        """Record one terminal outcome from the issued state."""
        if self._status is not RentalOrderItemStatus.ISSUED:
            raise InvalidRentalOrderItemStateError
        self._status = status
        self._charged_amount = charged_amount
        self._returned_at = returned_at
        self._return_note = return_note


@dataclass(slots=True, init=False)
class RentalOrder:
    """Aggregate root controlling one rental order and all item transitions."""

    _id: UUIDv7
    _order_number: str
    _customer_id: UUIDv7
    _customer_name_snapshot: str
    _customer_phone_snapshot: str
    _status: RentalOrderStatus
    _planned_start_at: datetime
    _planned_return_at: datetime
    _issued_at: datetime | None
    _closed_at: datetime | None
    _deposit_amount: Decimal
    _note: str | None
    _items: list[RentalOrderItem]

    def __init__(
        self,
        *,
        id: UUIDv7,
        order_number: str,
        customer_id: UUIDv7,
        customer_name_snapshot: str,
        customer_phone_snapshot: str,
        status: RentalOrderStatus,
        planned_start_at: datetime,
        planned_return_at: datetime,
        issued_at: datetime | None,
        closed_at: datetime | None,
        deposit_amount: Decimal,
        note: str | None,
        items: list[RentalOrderItem] | None = None,
    ) -> None:
        """Initialize aggregate state for creation or future rehydration."""
        self._id = id
        self._order_number = order_number
        self._customer_id = customer_id
        self._customer_name_snapshot = customer_name_snapshot
        self._customer_phone_snapshot = customer_phone_snapshot
        self._status = status
        self._planned_start_at = planned_start_at
        self._planned_return_at = planned_return_at
        self._issued_at = issued_at
        self._closed_at = closed_at
        self._deposit_amount = deposit_amount
        self._note = note
        self._items = list(items or [])

    @classmethod
    def create(
        cls,
        *,
        order_id: UUIDv7,
        order_number: str,
        customer_id: UUIDv7,
        customer_name_snapshot: str,
        customer_phone_snapshot: str,
        planned_start_at: datetime,
        planned_return_at: datetime,
        deposit_amount: Decimal,
        note: str | None = None,
    ) -> RentalOrder:
        """Create a valid empty draft order with immutable customer snapshots."""
        cls._require_text(order_number)
        cls._require_text(customer_name_snapshot)
        cls._require_text(customer_phone_snapshot)
        cls._require_period(planned_start_at, planned_return_at)
        normalized_deposit = cls._require_non_negative_money(deposit_amount)
        return cls(
            id=order_id,
            order_number=order_number.strip(),
            customer_id=customer_id,
            customer_name_snapshot=customer_name_snapshot.strip(),
            customer_phone_snapshot=customer_phone_snapshot.strip(),
            status=RentalOrderStatus.DRAFT,
            planned_start_at=planned_start_at,
            planned_return_at=planned_return_at,
            issued_at=None,
            closed_at=None,
            deposit_amount=normalized_deposit,
            note=cls._normalize_optional(note),
        )

    @property
    def id(self) -> UUIDv7:
        """Return the immutable technical identifier."""
        return self._id

    @property
    def order_number(self) -> str:
        """Return the immutable business-facing order number."""
        return self._order_number

    @property
    def customer_id(self) -> UUIDv7:
        """Return the referenced customer identifier."""
        return self._customer_id

    @property
    def customer_name_snapshot(self) -> str:
        """Return the immutable customer name captured at creation."""
        return self._customer_name_snapshot

    @property
    def customer_phone_snapshot(self) -> str:
        """Return the immutable customer phone captured at creation."""
        return self._customer_phone_snapshot

    @property
    def status(self) -> RentalOrderStatus:
        """Return the current aggregate lifecycle state."""
        return self._status

    @property
    def planned_start_at(self) -> datetime:
        """Return the planned rental start."""
        return self._planned_start_at

    @property
    def planned_return_at(self) -> datetime:
        """Return the planned rental return."""
        return self._planned_return_at

    @property
    def issued_at(self) -> datetime | None:
        """Return when the order was issued."""
        return self._issued_at

    @property
    def closed_at(self) -> datetime | None:
        """Return when every item reached a terminal outcome."""
        return self._closed_at

    @property
    def deposit_amount(self) -> Decimal:
        """Return the non-revenue deposit fixed for the order."""
        return self._deposit_amount

    @property
    def note(self) -> str | None:
        """Return the normalized order note."""
        return self._note

    @property
    def items(self) -> tuple[RentalOrderItem, ...]:
        """Return an immutable view of entities controlled by this aggregate."""
        return tuple(self._items)

    def add_item(
        self,
        *,
        item_id: UUIDv7,
        rental_asset_id: UUIDv7,
        asset_number_snapshot: str,
        title_snapshot: str,
        agreed_price: Decimal,
        discount: Decimal,
    ) -> RentalOrderItem:
        """Add one unique physical asset to a draft order."""
        self._require_status(RentalOrderStatus.DRAFT)
        if any(item.rental_asset_id == rental_asset_id for item in self._items):
            raise DuplicateRentalAssetError
        self._require_text(asset_number_snapshot)
        self._require_text(title_snapshot)
        normalized_price = self._require_non_negative_money(agreed_price)
        normalized_discount = self._require_non_negative_money(discount)
        if normalized_discount > normalized_price:
            raise InvalidRentalMoneyError
        item = RentalOrderItem(
            id=item_id,
            rental_asset_id=rental_asset_id,
            asset_number_snapshot=asset_number_snapshot.strip(),
            title_snapshot=title_snapshot.strip(),
            agreed_price=normalized_price,
            discount=normalized_discount,
        )
        self._items.append(item)
        return item

    def remove_item(self, item_id: UUIDv7) -> None:
        """Remove one prepared item from a draft order."""
        self._require_status(RentalOrderStatus.DRAFT)
        item = self._get_item(item_id)
        if item.status is not RentalOrderItemStatus.PREPARED:
            raise InvalidRentalOrderItemStateError
        self._items.remove(item)

    def change_period(self, planned_start_at: datetime, planned_return_at: datetime) -> None:
        """Change the planned period while the order remains a draft."""
        self._require_status(RentalOrderStatus.DRAFT)
        self._require_period(planned_start_at, planned_return_at)
        self._planned_start_at = planned_start_at
        self._planned_return_at = planned_return_at

    def set_deposit(self, deposit_amount: Decimal) -> None:
        """Change the non-revenue deposit while the order remains a draft."""
        self._require_status(RentalOrderStatus.DRAFT)
        self._deposit_amount = self._require_non_negative_money(deposit_amount)

    def update_note(self, note: str | None) -> None:
        """Change or clear the internal note while the order remains a draft."""
        self._require_status(RentalOrderStatus.DRAFT)
        self._note = self._normalize_optional(note)

    def issue(self, *, issued_at: datetime | None = None) -> None:
        """Issue every prepared item exactly once."""
        self._require_status(RentalOrderStatus.DRAFT)
        if not self._items:
            raise RentalOrderHasNoItemsError
        timestamp = issued_at or datetime.now(UTC)
        for item in self._items:
            item._issue()
        self._status = RentalOrderStatus.ISSUED
        self._issued_at = timestamp

    def return_item(
        self,
        item_id: UUIDv7,
        *,
        charged_amount: Decimal,
        returned_at: datetime | None = None,
        return_note: str | None = None,
    ) -> None:
        """Complete one issued item as returned and close when it is the last."""
        self._require_status(RentalOrderStatus.ISSUED)
        item = self._get_item(item_id)
        item._return(
            charged_amount=self._require_non_negative_money(charged_amount),
            returned_at=returned_at or datetime.now(UTC),
            return_note=self._normalize_optional(return_note),
        )
        self.close_if_completed(closed_at=returned_at)

    def mark_item_lost(
        self,
        item_id: UUIDv7,
        *,
        charged_amount: Decimal,
        completed_at: datetime | None = None,
        note: str | None = None,
    ) -> None:
        """Complete one issued item as lost and close when it is the last."""
        self._require_status(RentalOrderStatus.ISSUED)
        item = self._get_item(item_id)
        item._mark_lost(
            charged_amount=self._require_non_negative_money(charged_amount),
            completed_at=completed_at or datetime.now(UTC),
            note=self._normalize_optional(note),
        )
        self.close_if_completed(closed_at=completed_at)

    def cancel(self) -> None:
        """Cancel an unissued draft and all of its prepared items."""
        self._require_status(RentalOrderStatus.DRAFT)
        for item in self._items:
            item._cancel()
        self._status = RentalOrderStatus.CANCELLED

    def close_if_completed(self, *, closed_at: datetime | None = None) -> bool:
        """Close an issued order when every item has a terminal outcome."""
        self._require_status(RentalOrderStatus.ISSUED)
        if not self._items or any(
            item.status not in _COMPLETED_ITEM_STATUSES for item in self._items
        ):
            return False
        self._status = RentalOrderStatus.CLOSED
        self._closed_at = closed_at or datetime.now(UTC)
        return True

    def _get_item(self, item_id: UUIDv7) -> RentalOrderItem:
        """Return an item owned by this aggregate or raise a domain error."""
        item = next((candidate for candidate in self._items if candidate.id == item_id), None)
        if item is None:
            raise RentalOrderItemNotFoundError
        return item

    def _require_status(self, expected: RentalOrderStatus) -> None:
        """Reject a command outside its single allowed aggregate state."""
        if self._status is not expected:
            raise InvalidRentalOrderStateError

    @staticmethod
    def _require_period(planned_start_at: datetime, planned_return_at: datetime) -> None:
        """Require a strictly positive planned rental period."""
        if planned_return_at <= planned_start_at:
            raise InvalidRentalPeriodError

    @staticmethod
    def _require_non_negative_money(amount: Decimal) -> Decimal:
        """Normalize a monetary value and reject negative or non-Decimal input."""
        try:
            normalized = quantize_money(amount)
        except (TypeError, ArithmeticError) as exc:
            raise InvalidRentalMoneyError from exc
        if normalized < 0:
            raise InvalidRentalMoneyError
        return normalized

    @staticmethod
    def _require_text(value: str) -> None:
        """Reject required order or snapshot text without meaningful content."""
        if not value.strip():
            raise InvalidRentalOrderDataError

    @staticmethod
    def _normalize_optional(value: str | None) -> str | None:
        """Trim optional text and represent blank content as null."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
