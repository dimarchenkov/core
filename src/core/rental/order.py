from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from core.rental.order_enums import (
    RentalOrderItemStatus,
    RentalOrderStatus,
    RentalOverdueState,
    RentalReturnCondition,
    RentalTimingSource,
)
from core.rental.order_exceptions import (
    DuplicateRentalAssetError,
    ExpiredRentalSlotError,
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
    _rental_tariff_id: UUIDv7 | None
    _rental_tariff_version: int | None
    _timing_source: RentalTimingSource | None
    _tariff_snapshot: dict[str, str | int | bool | None] | None
    _asset_identification_snapshot: dict[str, object] | None
    _planned_rental_date: date | None
    _planned_start_at: datetime | None
    _issued_at: datetime | None
    _due_at: datetime | None
    _recommended_deposit: Decimal | None
    _charged_amount: Decimal | None
    _returned_at: datetime | None
    _return_note: str | None
    _return_condition: RentalReturnCondition | None
    _overdue_state: RentalOverdueState | None
    _overdue_minutes: int | None
    _overdue_charge: Decimal | None
    _damage_charge: Decimal | None
    _deposit_applied: Decimal | None
    _return_calculation_snapshot: dict[str, str | int | bool | None] | None
    _seal_verification_snapshot: list[dict[str, object]] | None
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
        rental_tariff_id: UUIDv7 | None = None,
        rental_tariff_version: int | None = None,
        timing_source: RentalTimingSource | None = None,
        tariff_snapshot: dict[str, str | int | bool | None] | None = None,
        asset_identification_snapshot: dict[str, object] | None = None,
        planned_rental_date: date | None = None,
        planned_start_at: datetime | None = None,
        issued_at: datetime | None = None,
        due_at: datetime | None = None,
        recommended_deposit: Decimal | None = None,
        charged_amount: Decimal | None = None,
        returned_at: datetime | None = None,
        return_note: str | None = None,
        return_condition: RentalReturnCondition | None = None,
        overdue_state: RentalOverdueState | None = None,
        overdue_minutes: int | None = None,
        overdue_charge: Decimal | None = None,
        damage_charge: Decimal | None = None,
        deposit_applied: Decimal | None = None,
        return_calculation_snapshot: dict[str, str | int | bool | None] | None = None,
        seal_verification_snapshot: list[dict[str, object]] | None = None,
        status: RentalOrderItemStatus = RentalOrderItemStatus.PREPARED,
    ) -> None:
        """Initialize entity state for aggregate construction or rehydration."""
        self._id = id
        self._rental_asset_id = rental_asset_id
        self._asset_number_snapshot = asset_number_snapshot
        self._title_snapshot = title_snapshot
        self._agreed_price = agreed_price
        self._discount = discount
        self._rental_tariff_id = rental_tariff_id
        self._rental_tariff_version = rental_tariff_version
        self._timing_source = timing_source
        self._tariff_snapshot = dict(tariff_snapshot) if tariff_snapshot is not None else None
        self._asset_identification_snapshot = (
            deepcopy(asset_identification_snapshot)
            if asset_identification_snapshot is not None
            else None
        )
        self._planned_rental_date = planned_rental_date
        self._planned_start_at = planned_start_at
        self._issued_at = issued_at
        self._due_at = due_at
        self._recommended_deposit = recommended_deposit
        self._charged_amount = charged_amount
        self._returned_at = returned_at
        self._return_note = return_note
        self._return_condition = return_condition
        self._overdue_state = overdue_state
        self._overdue_minutes = overdue_minutes
        self._overdue_charge = overdue_charge
        self._damage_charge = damage_charge
        self._deposit_applied = deposit_applied
        self._return_calculation_snapshot = (
            dict(return_calculation_snapshot) if return_calculation_snapshot is not None else None
        )
        self._seal_verification_snapshot = (
            [dict(value) for value in seal_verification_snapshot]
            if seal_verification_snapshot is not None
            else None
        )
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
    def rental_tariff_id(self) -> UUIDv7 | None:
        """Return the exact tariff version selected for this item."""
        return self._rental_tariff_id

    @property
    def rental_tariff_version(self) -> int | None:
        """Return the snapshotted tariff version number."""
        return self._rental_tariff_version

    @property
    def timing_source(self) -> RentalTimingSource | None:
        """Return whether this item's timing follows the tariff or a manual override."""
        return self._timing_source

    @property
    def tariff_snapshot(self) -> dict[str, str | int | bool | None] | None:
        """Return a defensive copy of this item's immutable commercial terms."""
        return dict(self._tariff_snapshot) if self._tariff_snapshot is not None else None

    @property
    def asset_identification_snapshot(self) -> dict[str, object] | None:
        """Return identification frozen at physical issue, independent of current asset data."""
        return (
            deepcopy(self._asset_identification_snapshot)
            if self._asset_identification_snapshot is not None
            else None
        )

    @property
    def planned_start_at(self) -> datetime | None:
        """Return a fixed contractual start for SLOT or manual timing."""
        return self._planned_start_at

    @property
    def planned_rental_date(self) -> date | None:
        """Return the operational date on which physical issue is planned."""
        return self._planned_rental_date

    @property
    def issued_at(self) -> datetime | None:
        """Return the actual physical issue time once the item is issued."""
        return self._issued_at

    @property
    def due_at(self) -> datetime | None:
        """Return the contractual due boundary once it is knowable."""
        return self._due_at

    @property
    def recommended_deposit(self) -> Decimal | None:
        """Return the deposit recommendation snapshotted for this item."""
        return self._recommended_deposit

    @property
    def is_configured(self) -> bool:
        """Return whether all tariff-based draft facts are complete."""
        base_complete = (
            self._rental_tariff_id is not None
            and self._rental_tariff_version is not None
            and self._timing_source is not None
            and self._tariff_snapshot is not None
            and self._planned_rental_date is not None
            and self._recommended_deposit is not None
        )
        if not base_complete:
            return False
        if self._timing_source is RentalTimingSource.MANUAL:
            return self._planned_start_at is not None and self._due_at is not None
        if self._tariff_snapshot.get("tariff_type") == "slot":
            return self._planned_start_at is not None and self._due_at is not None
        return self._planned_start_at is None and self._due_at is None

    def _configure(
        self,
        *,
        rental_asset_id: UUIDv7,
        asset_number_snapshot: str,
        title_snapshot: str,
        agreed_price: Decimal,
        rental_tariff_id: UUIDv7,
        rental_tariff_version: int,
        timing_source: RentalTimingSource,
        tariff_snapshot: dict[str, str | int | bool | None],
        planned_rental_date: date,
        planned_start_at: datetime | None,
        due_at: datetime | None,
        recommended_deposit: Decimal,
    ) -> None:
        """Replace the editable contractual facts of this one prepared item."""
        if self._status is not RentalOrderItemStatus.PREPARED:
            raise InvalidRentalOrderItemStateError
        self._rental_asset_id = rental_asset_id
        self._asset_number_snapshot = asset_number_snapshot
        self._title_snapshot = title_snapshot
        self._agreed_price = agreed_price
        self._discount = Decimal("0.00")
        self._rental_tariff_id = rental_tariff_id
        self._rental_tariff_version = rental_tariff_version
        self._timing_source = timing_source
        self._tariff_snapshot = dict(tariff_snapshot)
        self._planned_rental_date = planned_rental_date
        self._planned_start_at = planned_start_at
        self._issued_at = None
        self._due_at = due_at
        self._recommended_deposit = recommended_deposit

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
    def return_condition(self) -> RentalReturnCondition | None:
        """Return the explicit operator inspection outcome."""
        return self._return_condition

    @property
    def overdue_state(self) -> RentalOverdueState | None:
        """Return the lateness state frozen at the authoritative return timestamp."""
        return self._overdue_state

    @property
    def overdue_minutes(self) -> int | None:
        """Return the rounded-up overdue duration frozen for audit."""
        return self._overdue_minutes

    @property
    def overdue_charge(self) -> Decimal | None:
        """Return the immutable fixed-period overdue charge."""
        return self._overdue_charge

    @property
    def damage_charge(self) -> Decimal | None:
        """Return the manual damage, loss, or incomplete-set charge."""
        return self._damage_charge

    @property
    def deposit_applied(self) -> Decimal | None:
        """Return the order deposit provisionally or finally applied to this item."""
        return self._deposit_applied

    @property
    def return_calculation_snapshot(self) -> dict[str, str | int | bool | None] | None:
        """Return a defensive copy of the exact rule and amounts used at return."""
        return (
            dict(self._return_calculation_snapshot)
            if self._return_calculation_snapshot is not None
            else None
        )

    @property
    def seal_verification_snapshot(self) -> list[dict[str, object]] | None:
        """Return the immutable seal observations recorded during return."""
        return (
            [dict(value) for value in self._seal_verification_snapshot]
            if self._seal_verification_snapshot is not None
            else None
        )

    @property
    def status(self) -> RentalOrderItemStatus:
        """Return the current item lifecycle state."""
        return self._status

    def _issue(
        self,
        issued_at: datetime,
        asset_identification_snapshot: dict[str, object] | None = None,
    ) -> None:
        """Transition a prepared item as part of aggregate issue."""
        if self._status is not RentalOrderItemStatus.PREPARED:
            raise InvalidRentalOrderItemStateError
        self._issued_at = issued_at
        self._due_at = self.due_at_for_issue(issued_at)
        self._asset_identification_snapshot = (
            deepcopy(asset_identification_snapshot)
            if asset_identification_snapshot is not None
            else None
        )
        self._status = RentalOrderItemStatus.ISSUED

    def due_at_for_issue(self, issued_at: datetime) -> datetime | None:
        """Project the item due boundary from an actual issue timestamp."""
        if (
            self._timing_source is RentalTimingSource.TARIFF
            and self._tariff_snapshot is not None
            and self._tariff_snapshot.get("tariff_type") == "duration"
        ):
            duration_minutes = self._tariff_snapshot.get("duration_minutes")
            if not isinstance(duration_minutes, int) or duration_minutes <= 0:
                raise InvalidRentalOrderDataError
            return issued_at + timedelta(minutes=duration_minutes)
        return self._due_at

    def _return(
        self,
        *,
        charged_amount: Decimal,
        returned_at: datetime,
        return_note: str | None,
        return_condition: RentalReturnCondition | None = None,
        overdue_state: RentalOverdueState | None = None,
        overdue_minutes: int | None = None,
        overdue_charge: Decimal | None = None,
        damage_charge: Decimal | None = None,
        deposit_applied: Decimal | None = None,
        return_calculation_snapshot: dict[str, str | int | bool | None] | None = None,
        seal_verification_snapshot: list[dict[str, object]] | None = None,
    ) -> None:
        """Complete an issued item as physically returned."""
        self._complete(
            status=RentalOrderItemStatus.RETURNED,
            charged_amount=charged_amount,
            returned_at=returned_at,
            return_note=return_note,
            return_condition=return_condition,
            overdue_state=overdue_state,
            overdue_minutes=overdue_minutes,
            overdue_charge=overdue_charge,
            damage_charge=damage_charge,
            deposit_applied=deposit_applied,
            return_calculation_snapshot=return_calculation_snapshot,
            seal_verification_snapshot=seal_verification_snapshot,
        )

    def _mark_lost(
        self,
        *,
        charged_amount: Decimal,
        completed_at: datetime,
        note: str | None,
        damage_charge: Decimal | None = None,
        deposit_applied: Decimal | None = None,
        overdue_state: RentalOverdueState | None = None,
        overdue_minutes: int | None = None,
        overdue_charge: Decimal | None = None,
        return_calculation_snapshot: dict[str, str | int | bool | None] | None = None,
        seal_verification_snapshot: list[dict[str, object]] | None = None,
    ) -> None:
        """Complete an issued item as lost without implying physical return."""
        self._complete(
            status=RentalOrderItemStatus.LOST,
            charged_amount=charged_amount,
            returned_at=completed_at,
            return_note=note,
            return_condition=RentalReturnCondition.LOST,
            overdue_state=overdue_state,
            overdue_minutes=overdue_minutes,
            overdue_charge=overdue_charge,
            damage_charge=damage_charge,
            deposit_applied=deposit_applied,
            return_calculation_snapshot=return_calculation_snapshot,
            seal_verification_snapshot=seal_verification_snapshot,
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
        return_condition: RentalReturnCondition | None = None,
        overdue_state: RentalOverdueState | None = None,
        overdue_minutes: int | None = None,
        overdue_charge: Decimal | None = None,
        damage_charge: Decimal | None = None,
        deposit_applied: Decimal | None = None,
        return_calculation_snapshot: dict[str, str | int | bool | None] | None = None,
        seal_verification_snapshot: list[dict[str, object]] | None = None,
    ) -> None:
        """Record one terminal outcome from the issued state."""
        if self._status is not RentalOrderItemStatus.ISSUED:
            raise InvalidRentalOrderItemStateError
        self._status = status
        self._charged_amount = charged_amount
        self._returned_at = returned_at
        self._return_note = return_note
        self._return_condition = return_condition
        self._overdue_state = overdue_state
        self._overdue_minutes = overdue_minutes
        self._overdue_charge = overdue_charge
        self._damage_charge = damage_charge
        self._deposit_applied = deposit_applied
        self._return_calculation_snapshot = (
            dict(return_calculation_snapshot) if return_calculation_snapshot is not None else None
        )
        self._seal_verification_snapshot = (
            [dict(value) for value in seal_verification_snapshot]
            if seal_verification_snapshot is not None
            else None
        )


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
    _deposit_amount: Decimal | None
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
        deposit_amount: Decimal | None,
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
        self._deposit_amount = (
            self._require_non_negative_money(deposit_amount)
            if deposit_amount is not None
            else None
        )
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
        deposit_amount: Decimal | None = None,
        note: str | None = None,
    ) -> RentalOrder:
        """Create a valid empty draft order with immutable customer snapshots."""
        cls._require_text(order_number)
        cls._require_text(customer_name_snapshot)
        cls._require_text(customer_phone_snapshot)
        cls._require_period(planned_start_at, planned_return_at)
        normalized_deposit = (
            cls._require_non_negative_money(deposit_amount)
            if deposit_amount is not None
            else None
        )
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
    def deposit_amount(self) -> Decimal | None:
        """Return the confirmed receipt using the legacy API name."""
        return self._deposit_amount

    @property
    def deposit_received(self) -> Decimal | None:
        """Return actual money explicitly confirmed as received from the customer."""
        return self._deposit_amount

    @property
    def deposit_received_confirmed(self) -> bool:
        """Distinguish an explicit zero receipt from an unconfirmed receipt."""
        return self._deposit_amount is not None

    @property
    def note(self) -> str | None:
        """Return the normalized order note."""
        return self._note

    @property
    def items(self) -> tuple[RentalOrderItem, ...]:
        """Return an immutable view of entities controlled by this aggregate."""
        return tuple(self._items)

    @property
    def rental_total(self) -> Decimal:
        """Derive the agreed order total from every item."""
        return quantize_money(
            sum((item.agreed_price - item.discount for item in self._items), Decimal("0"))
        )

    @property
    def recommended_deposit_total(self) -> Decimal:
        """Keep the established API name as an alias for the contractual total."""
        return self.required_deposit_total

    @property
    def required_deposit_total(self) -> Decimal:
        """Derive the contractual order deposit from immutable item tariff snapshots."""
        total = Decimal("0")
        for item in self._items:
            snapshot = item.tariff_snapshot
            if snapshot is None:
                continue
            if "deposit_amount" not in snapshot:
                raise InvalidRentalOrderDataError
            raw_deposit = snapshot["deposit_amount"]
            if raw_deposit is None:
                continue
            try:
                deposit = Decimal(str(raw_deposit))
            except (ValueError, ArithmeticError) as error:
                raise InvalidRentalMoneyError from error
            total += self._require_non_negative_money(deposit)
        return quantize_money(total)

    @property
    def requires_agreement(self) -> bool:
        """Return whether any configured item requires an agreement."""
        return any(
            bool(item.tariff_snapshot and item.tariff_snapshot.get("requires_agreement"))
            for item in self._items
        )

    @property
    def overdue_charge_total(self) -> Decimal:
        """Sum immutable overdue charges for completed items."""
        return quantize_money(
            sum((item.overdue_charge or Decimal("0") for item in self._items), Decimal("0"))
        )

    @property
    def damage_charge_total(self) -> Decimal:
        """Sum immutable manual return charges for completed items."""
        return quantize_money(
            sum((item.damage_charge or Decimal("0") for item in self._items), Decimal("0"))
        )

    @property
    def deposit_applied_total(self) -> Decimal:
        """Sum the deposit allocation already attached to returned items."""
        return quantize_money(
            sum((item.deposit_applied or Decimal("0") for item in self._items), Decimal("0"))
        )

    @property
    def deposit_settlement_finalized(self) -> bool:
        """Return whether every item is terminal and the deposit may be released."""
        return self._status is RentalOrderStatus.CLOSED

    @property
    def deposit_to_refund(self) -> Decimal | None:
        """Return the final refundable deposit only after all items are completed."""
        if not self.deposit_settlement_finalized or self._deposit_amount is None:
            return None
        return quantize_money(max(self._deposit_amount - self.deposit_applied_total, Decimal("0")))

    @property
    def remaining_amount_due(self) -> Decimal | None:
        """Return final uncovered return liabilities only after order completion."""
        if not self.deposit_settlement_finalized or self._deposit_amount is None:
            return None
        liabilities = self.overdue_charge_total + self.damage_charge_total
        return quantize_money(max(liabilities - self.deposit_applied_total, Decimal("0")))

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
        self._refresh_draft_state()
        return item

    def remove_item(self, item_id: UUIDv7) -> None:
        """Remove one prepared item from a draft order."""
        self._require_editable()
        item = self._get_item(item_id)
        if item.status is not RentalOrderItemStatus.PREPARED:
            raise InvalidRentalOrderItemStateError
        self._items.remove(item)
        self._refresh_draft_state()

    def change_period(self, planned_start_at: datetime, planned_return_at: datetime) -> None:
        """Change the planned period while the order remains a draft."""
        self._require_status(RentalOrderStatus.DRAFT)
        self._require_period(planned_start_at, planned_return_at)
        self._planned_start_at = planned_start_at
        self._planned_return_at = planned_return_at

    def set_deposit_received(self, deposit_received: Decimal | None) -> None:
        """Confirm or clear the actual receipt independently of contractual terms."""
        if self._status not in {
            RentalOrderStatus.DRAFT,
            RentalOrderStatus.READY,
            RentalOrderStatus.ISSUED,
        }:
            raise InvalidRentalOrderStateError
        self._deposit_amount = (
            self._require_non_negative_money(deposit_received)
            if deposit_received is not None
            else None
        )

    def set_deposit(self, deposit_amount: Decimal | None) -> None:
        """Keep the legacy command as an alias for explicit receipt confirmation."""
        self.set_deposit_received(deposit_amount)

    def update_note(self, note: str | None) -> None:
        """Change or clear the internal note before physical issue."""
        self._require_editable()
        self._note = self._normalize_optional(note)

    def add_configured_item(
        self,
        *,
        item_id: UUIDv7,
        rental_asset_id: UUIDv7,
        asset_number_snapshot: str,
        title_snapshot: str,
        agreed_price: Decimal,
        planned_rental_date: date,
        planned_start_at: datetime | None,
        planned_return_at: datetime | None,
        deposit_amount: Decimal,
        rental_tariff_id: UUIDv7,
        rental_tariff_version: int,
        timing_source: RentalTimingSource,
        tariff_snapshot: dict[str, str | int | bool | None],
    ) -> RentalOrderItem:
        """Add one complete tariff-based item without changing its siblings."""
        self._require_editable()
        if any(item.rental_asset_id == rental_asset_id for item in self._items):
            raise DuplicateRentalAssetError
        item = self._build_configured_item(
            item_id=item_id,
            rental_asset_id=rental_asset_id,
            asset_number_snapshot=asset_number_snapshot,
            title_snapshot=title_snapshot,
            agreed_price=agreed_price,
            planned_rental_date=planned_rental_date,
            planned_start_at=planned_start_at,
            due_at=planned_return_at,
            recommended_deposit=deposit_amount,
            rental_tariff_id=rental_tariff_id,
            rental_tariff_version=rental_tariff_version,
            timing_source=timing_source,
            tariff_snapshot=tariff_snapshot,
        )
        self._items.append(item)
        self._refresh_draft_state()
        return item

    def update_configured_item(
        self,
        *,
        item_id: UUIDv7,
        rental_asset_id: UUIDv7,
        asset_number_snapshot: str,
        title_snapshot: str,
        agreed_price: Decimal,
        planned_rental_date: date,
        planned_start_at: datetime | None,
        planned_return_at: datetime | None,
        deposit_amount: Decimal,
        rental_tariff_id: UUIDv7,
        rental_tariff_version: int,
        timing_source: RentalTimingSource,
        tariff_snapshot: dict[str, str | int | bool | None],
    ) -> RentalOrderItem:
        """Edit one prepared item while preserving all sibling items."""
        self._require_editable()
        item = self._get_item(item_id)
        if any(
            sibling.id != item_id and sibling.rental_asset_id == rental_asset_id
            for sibling in self._items
        ):
            raise DuplicateRentalAssetError
        self._require_item_timing(
            planned_start_at, planned_return_at, timing_source, tariff_snapshot
        )
        self._require_text(asset_number_snapshot)
        self._require_text(title_snapshot)
        if rental_tariff_version < 1 or not tariff_snapshot:
            raise InvalidRentalOrderDataError
        item._configure(
            rental_asset_id=rental_asset_id,
            asset_number_snapshot=asset_number_snapshot.strip(),
            title_snapshot=title_snapshot.strip(),
            agreed_price=self._require_non_negative_money(agreed_price),
            rental_tariff_id=rental_tariff_id,
            rental_tariff_version=rental_tariff_version,
            timing_source=timing_source,
            tariff_snapshot=tariff_snapshot,
            planned_rental_date=planned_rental_date,
            planned_start_at=planned_start_at,
            due_at=planned_return_at,
            recommended_deposit=self._require_non_negative_money(deposit_amount),
        )
        self._refresh_draft_state()
        return item

    def _build_configured_item(
        self,
        *,
        item_id: UUIDv7,
        rental_asset_id: UUIDv7,
        asset_number_snapshot: str,
        title_snapshot: str,
        agreed_price: Decimal,
        planned_rental_date: date,
        planned_start_at: datetime | None,
        due_at: datetime | None,
        recommended_deposit: Decimal,
        rental_tariff_id: UUIDv7,
        rental_tariff_version: int,
        timing_source: RentalTimingSource,
        tariff_snapshot: dict[str, str | int | bool | None],
    ) -> RentalOrderItem:
        """Validate and construct one complete item."""
        self._require_item_timing(planned_start_at, due_at, timing_source, tariff_snapshot)
        self._require_text(asset_number_snapshot)
        self._require_text(title_snapshot)
        if rental_tariff_version < 1 or not tariff_snapshot:
            raise InvalidRentalOrderDataError
        return RentalOrderItem(
            id=item_id,
            rental_asset_id=rental_asset_id,
            asset_number_snapshot=asset_number_snapshot.strip(),
            title_snapshot=title_snapshot.strip(),
            agreed_price=self._require_non_negative_money(agreed_price),
            discount=Decimal("0.00"),
            rental_tariff_id=rental_tariff_id,
            rental_tariff_version=rental_tariff_version,
            timing_source=timing_source,
            tariff_snapshot=tariff_snapshot,
            planned_rental_date=planned_rental_date,
            planned_start_at=planned_start_at,
            due_at=due_at,
            recommended_deposit=self._require_non_negative_money(recommended_deposit),
        )

    def _refresh_draft_state(self) -> None:
        """Recompute readiness and legacy summary boundaries from all items."""
        self._status = (
            RentalOrderStatus.READY
            if self._items and all(item.is_configured for item in self._items)
            else RentalOrderStatus.DRAFT
        )
        configured = [item for item in self._items if item.is_configured]
        if configured:
            starts = [item.planned_start_at for item in configured if item.planned_start_at]
            dues = [item.due_at for item in configured if item.due_at]
            if starts:
                self._planned_start_at = min(starts, key=self._datetime_sort_key)
            if dues:
                self._planned_return_at = max(dues, key=self._datetime_sort_key)

    @staticmethod
    def _datetime_sort_key(value: datetime) -> datetime:
        """Normalize SQLite-rehydrated naive datetimes for comparisons."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def issue(
        self,
        *,
        issued_at: datetime | None = None,
        identification_by_item: dict[UUIDv7, dict[str, object]] | None = None,
    ) -> None:
        """Issue every prepared item exactly once."""
        if self._status not in {RentalOrderStatus.DRAFT, RentalOrderStatus.READY}:
            raise InvalidRentalOrderStateError
        if not self._items:
            raise RentalOrderHasNoItemsError
        timestamp = issued_at or datetime.now(UTC)
        for item in self._items:
            if item.status is not RentalOrderItemStatus.PREPARED:
                raise InvalidRentalOrderItemStateError
            if (
                item.timing_source is RentalTimingSource.TARIFF
                and item.tariff_snapshot is not None
                and item.tariff_snapshot.get("tariff_type") == "slot"
                and (
                    item.due_at is None
                    or self._datetime_sort_key(timestamp) >= self._datetime_sort_key(item.due_at)
                )
            ):
                raise ExpiredRentalSlotError
        for item in self._items:
            item._issue(timestamp, (identification_by_item or {}).get(item.id))
        self._status = RentalOrderStatus.ISSUED
        self._issued_at = timestamp
        self._refresh_summary_boundaries()

    def _refresh_summary_boundaries(self) -> None:
        """Update compatibility summaries from exact item boundaries when available."""
        starts = [item.issued_at or item.planned_start_at for item in self._items]
        starts = [value for value in starts if value is not None]
        dues = [item.due_at for item in self._items if item.due_at is not None]
        if starts:
            self._planned_start_at = min(starts, key=self._datetime_sort_key)
        if dues:
            self._planned_return_at = max(dues, key=self._datetime_sort_key)

    def return_item(
        self,
        item_id: UUIDv7,
        *,
        charged_amount: Decimal,
        returned_at: datetime | None = None,
        return_note: str | None = None,
        return_condition: RentalReturnCondition | None = None,
        overdue_state: RentalOverdueState | None = None,
        overdue_minutes: int | None = None,
        overdue_charge: Decimal | None = None,
        damage_charge: Decimal | None = None,
        deposit_applied: Decimal | None = None,
        return_calculation_snapshot: dict[str, str | int | bool | None] | None = None,
        seal_verification_snapshot: list[dict[str, object]] | None = None,
    ) -> None:
        """Complete one issued item as returned and close when it is the last."""
        self._require_status(RentalOrderStatus.ISSUED)
        item = self._get_item(item_id)
        item._return(
            charged_amount=self._require_non_negative_money(charged_amount),
            returned_at=returned_at or datetime.now(UTC),
            return_note=self._normalize_optional(return_note),
            return_condition=return_condition,
            overdue_state=overdue_state,
            overdue_minutes=overdue_minutes,
            overdue_charge=(
                self._require_non_negative_money(overdue_charge)
                if overdue_charge is not None
                else None
            ),
            damage_charge=(
                self._require_non_negative_money(damage_charge)
                if damage_charge is not None
                else None
            ),
            deposit_applied=(
                self._require_non_negative_money(deposit_applied)
                if deposit_applied is not None
                else None
            ),
            return_calculation_snapshot=return_calculation_snapshot,
            seal_verification_snapshot=seal_verification_snapshot,
        )
        self.close_if_completed(closed_at=returned_at)

    def mark_item_lost(
        self,
        item_id: UUIDv7,
        *,
        charged_amount: Decimal,
        completed_at: datetime | None = None,
        note: str | None = None,
        damage_charge: Decimal | None = None,
        deposit_applied: Decimal | None = None,
        overdue_state: RentalOverdueState | None = None,
        overdue_minutes: int | None = None,
        overdue_charge: Decimal | None = None,
        return_calculation_snapshot: dict[str, str | int | bool | None] | None = None,
        seal_verification_snapshot: list[dict[str, object]] | None = None,
    ) -> None:
        """Complete one issued item as lost and close when it is the last."""
        self._require_status(RentalOrderStatus.ISSUED)
        item = self._get_item(item_id)
        item._mark_lost(
            charged_amount=self._require_non_negative_money(charged_amount),
            completed_at=completed_at or datetime.now(UTC),
            note=self._normalize_optional(note),
            overdue_state=overdue_state,
            overdue_minutes=overdue_minutes,
            overdue_charge=(
                self._require_non_negative_money(overdue_charge)
                if overdue_charge is not None
                else None
            ),
            damage_charge=(
                self._require_non_negative_money(damage_charge)
                if damage_charge is not None
                else None
            ),
            deposit_applied=(
                self._require_non_negative_money(deposit_applied)
                if deposit_applied is not None
                else None
            ),
            return_calculation_snapshot=return_calculation_snapshot,
            seal_verification_snapshot=seal_verification_snapshot,
        )
        self.close_if_completed(closed_at=completed_at)

    def cancel(self) -> None:
        """Cancel an unissued draft and all of its prepared items."""
        self._require_editable()
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

    def _require_editable(self) -> None:
        """Allow contractual editing only before physical issue."""
        if self._status not in {RentalOrderStatus.DRAFT, RentalOrderStatus.READY}:
            raise InvalidRentalOrderStateError

    @staticmethod
    def _require_period(planned_start_at: datetime, planned_return_at: datetime) -> None:
        """Require a strictly positive planned rental period."""
        if planned_return_at <= planned_start_at:
            raise InvalidRentalPeriodError

    @classmethod
    def _require_item_timing(
        cls,
        planned_start_at: datetime | None,
        due_at: datetime | None,
        timing_source: RentalTimingSource,
        tariff_snapshot: dict[str, str | int | bool | None],
    ) -> None:
        """Validate fixed timing only when the selected mode defines it before issue."""
        fixed_timing = (
            timing_source is RentalTimingSource.MANUAL
            or tariff_snapshot.get("tariff_type") == "slot"
        )
        if fixed_timing:
            if planned_start_at is None or due_at is None:
                raise InvalidRentalPeriodError
            cls._require_period(planned_start_at, due_at)
        elif planned_start_at is not None or due_at is not None:
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
