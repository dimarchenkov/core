from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from core.rental.order_enums import RentalOverduePolicy, RentalOverdueState
from core.shared.money import quantize_money


class RentalReturnRuleError(ValueError):
    """Raised when a historical snapshot cannot define deterministic overdue billing."""


@dataclass(frozen=True, slots=True)
class RentalOverdueRule:
    """The exact billing rule resolved only from immutable item terms."""

    policy: RentalOverduePolicy
    grace_minutes: int
    unit_minutes: int
    unit_price: Decimal


@dataclass(frozen=True, slots=True)
class RentalReturnCalculation:
    """Pure monetary and timing result for one item return."""

    overdue_state: RentalOverdueState
    overdue_minutes: int
    overdue_blocks: int
    base_rental_price: Decimal
    overdue_charge: Decimal
    damage_charge: Decimal
    total_charge: Decimal
    rule: RentalOverdueRule


def calculate_return(
    *,
    tariff_snapshot: dict[str, str | int | bool | None],
    agreed_price: Decimal,
    discount: Decimal,
    due_at: datetime,
    returned_at: datetime,
    damage_charge: Decimal = Decimal("0"),
) -> RentalReturnCalculation:
    """Calculate one fixed-period return without current tariff reads or proration."""
    rule = resolve_overdue_rule(tariff_snapshot, agreed_price=agreed_price)
    normalized_damage = _non_negative_money(damage_charge)
    base_price = _non_negative_money(agreed_price - discount)
    due = _utc(due_at)
    returned = _utc(returned_at)
    overdue_seconds = max(0, int((returned - due).total_seconds()))
    overdue_minutes = (overdue_seconds + 59) // 60
    grace_seconds = rule.grace_minutes * 60

    if overdue_seconds == 0:
        state = RentalOverdueState.ON_TIME
        blocks = 0
    elif overdue_seconds <= grace_seconds:
        state = RentalOverdueState.OVERDUE_GRACE
        blocks = 0
    else:
        state = RentalOverdueState.OVERDUE_CHARGEABLE
        chargeable_seconds = overdue_seconds - grace_seconds
        unit_seconds = rule.unit_minutes * 60
        blocks = (chargeable_seconds + unit_seconds - 1) // unit_seconds

    overdue_charge = quantize_money(rule.unit_price * blocks)
    return RentalReturnCalculation(
        overdue_state=state,
        overdue_minutes=overdue_minutes,
        overdue_blocks=blocks,
        base_rental_price=base_price,
        overdue_charge=overdue_charge,
        damage_charge=normalized_damage,
        total_charge=quantize_money(base_price + overdue_charge + normalized_damage),
        rule=rule,
    )


def resolve_overdue_rule(
    snapshot: dict[str, str | int | bool | None],
    *,
    agreed_price: Decimal,
) -> RentalOverdueRule:
    """Resolve the explicit next-period rule from a frozen tariff snapshot."""
    policy_value = str(snapshot.get("overdue_policy") or RentalOverduePolicy.NEXT_PERIOD.value)
    try:
        policy = RentalOverduePolicy(policy_value)
    except ValueError as exc:
        raise RentalReturnRuleError("Unsupported snapshot overdue policy.") from exc

    grace_minutes = _positive_or_zero_int(snapshot.get("grace_minutes"), "grace_minutes")
    unit_minutes = snapshot.get("overdue_unit_minutes")
    if not isinstance(unit_minutes, int) or unit_minutes <= 0:
        unit_minutes = _snapshot_period_minutes(snapshot)

    price_value = snapshot.get("overdue_unit_price")
    if price_value is None:
        price_value = snapshot.get("agreed_price") or snapshot.get("tariff_price")
    try:
        unit_price = _non_negative_money(
            Decimal(str(price_value)) if price_value is not None else agreed_price
        )
    except (ArithmeticError, ValueError) as exc:
        raise RentalReturnRuleError("Snapshot overdue price is invalid.") from exc
    return RentalOverdueRule(
        policy=policy,
        grace_minutes=grace_minutes,
        unit_minutes=unit_minutes,
        unit_price=unit_price,
    )


def projected_overdue_state(
    *,
    due_at: datetime | None,
    grace_minutes: int,
    now: datetime,
) -> tuple[RentalOverdueState, int]:
    """Derive the live non-persisted workspace state for one active item."""
    if due_at is None:
        return RentalOverdueState.ON_TIME, 0
    overdue_seconds = max(0, int((_utc(now) - _utc(due_at)).total_seconds()))
    overdue_minutes = (overdue_seconds + 59) // 60
    if overdue_seconds == 0:
        return RentalOverdueState.ON_TIME, 0
    if overdue_seconds <= max(grace_minutes, 0) * 60:
        return RentalOverdueState.OVERDUE_GRACE, overdue_minutes
    return RentalOverdueState.OVERDUE_CHARGEABLE, overdue_minutes


def _snapshot_period_minutes(snapshot: dict[str, str | int | bool | None]) -> int:
    """Derive one billing block from DURATION or same-day SLOT snapshot fields."""
    duration = snapshot.get("duration_minutes")
    if isinstance(duration, int) and duration > 0:
        return duration
    start = snapshot.get("slot_start")
    end = snapshot.get("slot_end")
    if isinstance(start, str) and isinstance(end, str):
        try:
            start_hour, start_minute = (int(part) for part in start.split(":"))
            end_hour, end_minute = (int(part) for part in end.split(":"))
        except (TypeError, ValueError) as exc:
            raise RentalReturnRuleError("Snapshot SLOT boundaries are invalid.") from exc
        minutes = end_hour * 60 + end_minute - (start_hour * 60 + start_minute)
        if minutes > 0:
            return minutes
    raise RentalReturnRuleError("Snapshot does not contain an overdue billing period.")


def _positive_or_zero_int(value: object, field: str) -> int:
    """Validate a non-negative integer snapshot fact."""
    if not isinstance(value, int) or value < 0:
        raise RentalReturnRuleError(f"Snapshot {field} is invalid.")
    return value


def _non_negative_money(value: Decimal) -> Decimal:
    """Normalize money and reject negative or non-finite values."""
    try:
        normalized = quantize_money(value)
    except (ArithmeticError, TypeError) as exc:
        raise RentalReturnRuleError("Return money must be finite.") from exc
    if normalized < 0:
        raise RentalReturnRuleError("Return money cannot be negative.")
    return normalized


def _utc(value: datetime) -> datetime:
    """Normalize persisted aware timestamps while tolerating SQLite timezone loss."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
