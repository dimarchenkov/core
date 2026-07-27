from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from core.rental import (
    DuplicateRentalAssetError,
    InvalidRentalMoneyError,
    InvalidRentalOrderItemStateError,
    InvalidRentalOrderStateError,
    InvalidRentalPeriodError,
    RentalOrder,
    RentalOrderHasNoItemsError,
    RentalOrderItemStatus,
    RentalOrderStatus,
)

ORDER_ID = UUID("019c0000-0000-7000-8000-000000000201")
CUSTOMER_ID = UUID("019c0000-0000-7000-8000-000000000202")
ITEM_1_ID = UUID("019c0000-0000-7000-8000-000000000203")
ITEM_2_ID = UUID("019c0000-0000-7000-8000-000000000204")
ASSET_1_ID = UUID("019c0000-0000-7000-8000-000000000205")
ASSET_2_ID = UUID("019c0000-0000-7000-8000-000000000206")
START_AT = datetime(2026, 7, 28, 10, tzinfo=UTC)
RETURN_AT = START_AT + timedelta(days=2)


def create_order(*, deposit: Decimal = Decimal("1000")) -> RentalOrder:
    """Create one deterministic draft aggregate for focused domain tests."""
    return RentalOrder.create(
        order_id=ORDER_ID,
        order_number=" RENT-ORDER-000001 ",
        customer_id=CUSTOMER_ID,
        customer_name_snapshot=" Иван Иванов ",
        customer_phone_snapshot=" +79991234567 ",
        planned_start_at=START_AT,
        planned_return_at=RETURN_AT,
        deposit_amount=deposit,
        note="  Бережное использование  ",
    )


def add_first_item(order: RentalOrder) -> None:
    """Add the first deterministic asset to an order."""
    order.add_item(
        item_id=ITEM_1_ID,
        rental_asset_id=ASSET_1_ID,
        asset_number_snapshot=" RENT-000001 ",
        title_snapshot=" Шуруповерт Makita ",
        agreed_price=Decimal("700.005"),
        discount=Decimal("50"),
    )


def add_second_item(order: RentalOrder) -> None:
    """Add the second deterministic asset to an order."""
    order.add_item(
        item_id=ITEM_2_ID,
        rental_asset_id=ASSET_2_ID,
        asset_number_snapshot="RENT-000002",
        title_snapshot="Пылесос Karcher",
        agreed_price=Decimal("900"),
        discount=Decimal("0"),
    )


def test_create_draft_order_normalizes_domain_data() -> None:
    """A valid order starts empty in DRAFT with normalized money and snapshots."""
    order = create_order(deposit=Decimal("1000.005"))

    assert order.id == ORDER_ID
    assert order.order_number == "RENT-ORDER-000001"
    assert order.customer_id == CUSTOMER_ID
    assert order.customer_name_snapshot == "Иван Иванов"
    assert order.customer_phone_snapshot == "+79991234567"
    assert order.status is RentalOrderStatus.DRAFT
    assert order.deposit_amount == Decimal("1000.01")
    assert order.note == "Бережное использование"
    assert order.items == ()
    assert order.issued_at is None
    assert order.closed_at is None


def test_create_rejects_invalid_period_and_negative_deposit() -> None:
    """Order creation protects period and deposit invariants."""
    with pytest.raises(InvalidRentalPeriodError):
        RentalOrder.create(
            order_id=ORDER_ID,
            order_number="RENT-ORDER-000001",
            customer_id=CUSTOMER_ID,
            customer_name_snapshot="Иван Иванов",
            customer_phone_snapshot="+79991234567",
            planned_start_at=START_AT,
            planned_return_at=START_AT,
            deposit_amount=Decimal("0"),
        )

    with pytest.raises(InvalidRentalMoneyError):
        create_order(deposit=Decimal("-0.01"))


def test_add_item_creates_read_only_prepared_entity() -> None:
    """Only the aggregate creates an item and exposes its state for reading."""
    order = create_order()

    item = order.add_item(
        item_id=ITEM_1_ID,
        rental_asset_id=ASSET_1_ID,
        asset_number_snapshot=" RENT-000001 ",
        title_snapshot=" Шуруповерт Makita ",
        agreed_price=Decimal("700.005"),
        discount=Decimal("50"),
    )

    assert item.status is RentalOrderItemStatus.PREPARED
    assert item.asset_number_snapshot == "RENT-000001"
    assert item.title_snapshot == "Шуруповерт Makita"
    assert item.agreed_price == Decimal("700.01")
    assert item.discount == Decimal("50.00")
    with pytest.raises(AttributeError):
        item.status = RentalOrderItemStatus.ISSUED  # type: ignore[misc]
    with pytest.raises(AttributeError):
        order.items = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("agreed_price", "discount"),
    [
        (Decimal("-1"), Decimal("0")),
        (Decimal("100"), Decimal("-1")),
        (Decimal("100"), Decimal("101")),
    ],
)
def test_add_item_rejects_invalid_money(
    agreed_price: Decimal,
    discount: Decimal,
) -> None:
    """A position cannot contain negative money or excessive discount."""
    order = create_order()

    with pytest.raises(InvalidRentalMoneyError):
        order.add_item(
            item_id=ITEM_1_ID,
            rental_asset_id=ASSET_1_ID,
            asset_number_snapshot="RENT-000001",
            title_snapshot="Шуруповерт",
            agreed_price=agreed_price,
            discount=discount,
        )


def test_duplicate_rental_asset_is_rejected() -> None:
    """One physical asset cannot appear in the same order twice."""
    order = create_order()
    add_first_item(order)

    with pytest.raises(DuplicateRentalAssetError):
        order.add_item(
            item_id=ITEM_2_ID,
            rental_asset_id=ASSET_1_ID,
            asset_number_snapshot="RENT-000001",
            title_snapshot="Шуруповерт",
            agreed_price=Decimal("700"),
            discount=Decimal("0"),
        )


def test_remove_item_from_draft() -> None:
    """A prepared item can be removed before issue."""
    order = create_order()
    add_first_item(order)

    order.remove_item(ITEM_1_ID)

    assert order.items == ()


def test_update_draft_period_deposit_and_note() -> None:
    """Approved draft details change only through aggregate commands."""
    order = create_order()
    new_start = START_AT + timedelta(days=1)
    new_return = RETURN_AT + timedelta(days=2)

    order.change_period(new_start, new_return)
    order.set_deposit(Decimal("1200.005"))
    order.update_note("  Обновлено  ")

    assert order.planned_start_at == new_start
    assert order.planned_return_at == new_return
    assert order.deposit_amount == Decimal("1200.01")
    assert order.note == "Обновлено"


def test_issued_order_rejects_draft_detail_changes() -> None:
    """Issued contractual details cannot be rewritten."""
    order = create_order()
    add_first_item(order)
    order.issue(issued_at=START_AT)

    with pytest.raises(InvalidRentalOrderStateError):
        order.change_period(START_AT, RETURN_AT)
    with pytest.raises(InvalidRentalOrderStateError):
        order.set_deposit(Decimal("0"))
    with pytest.raises(InvalidRentalOrderStateError):
        order.update_note(None)


def test_issue_empty_order_is_rejected() -> None:
    """A rental order cannot be issued without a physical asset."""
    with pytest.raises(RentalOrderHasNoItemsError):
        create_order().issue(issued_at=START_AT)


def test_successful_issue_transitions_order_and_all_items() -> None:
    """Issue moves the entire aggregate from draft to issued exactly once."""
    order = create_order()
    add_first_item(order)
    add_second_item(order)

    order.issue(issued_at=START_AT)

    assert order.status is RentalOrderStatus.ISSUED
    assert order.issued_at == START_AT
    assert all(item.status is RentalOrderItemStatus.ISSUED for item in order.items)


def test_second_issue_is_rejected() -> None:
    """An issued order cannot be issued again."""
    order = create_order()
    add_first_item(order)
    order.issue(issued_at=START_AT)

    with pytest.raises(InvalidRentalOrderStateError):
        order.issue(issued_at=START_AT)


def test_cancel_draft_cancels_prepared_items() -> None:
    """Cancelling a draft terminates the order and its unissued items."""
    order = create_order()
    add_first_item(order)

    order.cancel()

    assert order.status is RentalOrderStatus.CANCELLED
    assert order.items[0].status is RentalOrderItemStatus.CANCELLED


def test_cancel_issued_order_is_rejected() -> None:
    """An issued order must be completed through return or loss."""
    order = create_order()
    add_first_item(order)
    order.issue(issued_at=START_AT)

    with pytest.raises(InvalidRentalOrderStateError):
        order.cancel()


def test_partial_return_keeps_order_issued() -> None:
    """Returning one of several assets completes only the selected entity."""
    order = create_order()
    add_first_item(order)
    add_second_item(order)
    order.issue(issued_at=START_AT)

    order.return_item(
        ITEM_1_ID,
        charged_amount=Decimal("650"),
        returned_at=RETURN_AT,
        return_note="Исправен",
    )

    assert order.status is RentalOrderStatus.ISSUED
    assert order.items[0].status is RentalOrderItemStatus.RETURNED
    assert order.items[0].charged_amount == Decimal("650.00")
    assert order.items[0].returned_at == RETURN_AT
    assert order.items[1].status is RentalOrderItemStatus.ISSUED


def test_final_return_automatically_closes_order() -> None:
    """The last returned item closes the aggregate at the same business time."""
    order = create_order()
    add_first_item(order)
    add_second_item(order)
    order.issue(issued_at=START_AT)
    order.return_item(ITEM_1_ID, charged_amount=Decimal("650"), returned_at=RETURN_AT)
    final_return_at = RETURN_AT + timedelta(hours=1)

    order.return_item(
        ITEM_2_ID,
        charged_amount=Decimal("900"),
        returned_at=final_return_at,
    )

    assert order.status is RentalOrderStatus.CLOSED
    assert order.closed_at == final_return_at
    assert all(item.status is RentalOrderItemStatus.RETURNED for item in order.items)


def test_lost_item_counts_as_completion_and_closes_order() -> None:
    """A lost terminal outcome can close an otherwise completed order."""
    order = create_order()
    add_first_item(order)
    add_second_item(order)
    order.issue(issued_at=START_AT)
    order.return_item(ITEM_1_ID, charged_amount=Decimal("650"), returned_at=RETURN_AT)
    loss_at = RETURN_AT + timedelta(hours=2)

    order.mark_item_lost(
        ITEM_2_ID,
        charged_amount=Decimal("5000"),
        completed_at=loss_at,
        note="Не возвращен",
    )

    assert order.status is RentalOrderStatus.CLOSED
    assert order.closed_at == loss_at
    assert order.items[1].status is RentalOrderItemStatus.LOST
    assert order.items[1].return_note == "Не возвращен"


def test_remove_and_add_after_issue_are_rejected() -> None:
    """Issued composition is immutable."""
    order = create_order()
    add_first_item(order)
    order.issue(issued_at=START_AT)

    with pytest.raises(InvalidRentalOrderStateError):
        order.remove_item(ITEM_1_ID)
    with pytest.raises(InvalidRentalOrderStateError):
        add_second_item(order)


def test_return_item_twice_is_rejected() -> None:
    """A completed item cannot receive a second terminal outcome."""
    order = create_order()
    add_first_item(order)
    add_second_item(order)
    order.issue(issued_at=START_AT)
    order.return_item(ITEM_1_ID, charged_amount=Decimal("650"), returned_at=RETURN_AT)

    with pytest.raises(InvalidRentalOrderItemStateError):
        order.return_item(
            ITEM_1_ID,
            charged_amount=Decimal("650"),
            returned_at=RETURN_AT,
        )


def test_completed_order_is_read_only() -> None:
    """Closed aggregate rejects composition and lifecycle commands."""
    order = create_order()
    add_first_item(order)
    order.issue(issued_at=START_AT)
    order.return_item(ITEM_1_ID, charged_amount=Decimal("700"), returned_at=RETURN_AT)

    with pytest.raises(InvalidRentalOrderStateError):
        order.cancel()
    with pytest.raises(InvalidRentalOrderStateError):
        order.close_if_completed(closed_at=RETURN_AT)
