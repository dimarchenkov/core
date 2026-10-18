from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import (
    AliasChoices,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic import (
    BaseModel as PydanticBaseModel,
)

from core.rental.enums import (
    AssetCondition,
    RentalComponentVerificationResult,
    RentalSealVerificationResult,
)
from core.rental.order_enums import (
    RentalOrderItemStatus,
    RentalOrderStatus,
    RentalOverdueState,
    RentalReturnCondition,
    RentalTimingSource,
)
from core.rental.return_calculator import projected_overdue_state
from core.shared.db import UUIDv7


class RentalOrderCreate(PydanticBaseModel):
    """Payload for opening an empty rental order draft."""

    model_config = ConfigDict(extra="forbid")

    customer_id: UUIDv7
    planned_start_at: datetime | None = None
    planned_return_at: datetime | None = None
    deposit_received: Decimal | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("deposit_received", "deposit_amount"),
    )
    note: str | None = None

    @model_validator(mode="after")
    def validate_legacy_period(self) -> RentalOrderCreate:
        """Accept either both legacy period fields or neither for client-first drafts."""
        if (self.planned_start_at is None) != (self.planned_return_at is None):
            raise ValueError("planned_start_at and planned_return_at must be provided together")
        return self


class RentalOrderUpdate(PydanticBaseModel):
    """Payload for changing mutable draft details."""

    model_config = ConfigDict(extra="forbid")

    planned_start_at: datetime | None = None
    planned_return_at: datetime | None = None
    deposit_received: Decimal | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("deposit_received", "deposit_amount"),
    )
    note: str | None = None


class RentalDepositReceivedUpdate(PydanticBaseModel):
    """Explicit confirmation of the actual deposit physically received."""

    model_config = ConfigDict(extra="forbid")

    deposit_received: Decimal = Field(max_digits=12, ge=0)

    @field_validator("deposit_received", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        """Require confirmed money without binary-float ambiguity."""
        if isinstance(value, float):
            raise ValueError("Money must be sent as a decimal string or integer.")
        return value


class RentalOrderItemCreate(PydanticBaseModel):
    """Payload for adding one physical asset to a draft."""

    model_config = ConfigDict(extra="forbid")

    rental_asset_id: UUIDv7
    agreed_price: Decimal
    discount: Decimal = Decimal("0")


class RentalOrderTariffConfigure(PydanticBaseModel):
    """Configure one order item with a physical asset and exact tariff snapshot."""

    model_config = ConfigDict(extra="forbid")

    rental_asset_id: UUIDv7
    tariff_id: UUIDv7
    timing_source: RentalTimingSource = RentalTimingSource.TARIFF
    rental_date: date | None = None
    planned_start_at: datetime | None = None
    planned_return_at: datetime | None = None
    agreed_price: Decimal | None = Field(default=None, max_digits=12, ge=0)

    @field_validator("agreed_price", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        """Require negotiated money to arrive without binary-float ambiguity."""
        if isinstance(value, float):
            raise ValueError("Money must be sent as a decimal string or integer.")
        return value

    @model_validator(mode="after")
    def validate_manual_period(self) -> RentalOrderTariffConfigure:
        """Require both explicit boundaries only for a manual period override."""
        if self.timing_source is RentalTimingSource.MANUAL:
            if self.planned_start_at is None or self.planned_return_at is None:
                raise ValueError("manual timing requires planned start and return")
        elif self.planned_return_at is not None:
            raise ValueError("tariff timing does not accept a manual return")
        return self


class RentalOrderItemReturn(PydanticBaseModel):
    """Payload for completing one item as physically returned."""

    model_config = ConfigDict(extra="forbid")

    condition: AssetCondition
    charged_amount: Decimal
    returned_at: datetime | None = None
    return_note: str | None = None


class RentalOrderIssue(PydanticBaseModel):
    """Optional exceptional actual issue-time override."""

    model_config = ConfigDict(extra="forbid")

    issued_at: datetime | None = None

    @field_validator("issued_at")
    @classmethod
    def require_aware_issue_time(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous browser-local timestamps."""
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("issued_at must include a UTC offset")
        return value


class RentalIssuePreviewItem(PydanticBaseModel):
    """One item's server-authoritative issue projection."""

    id: UUIDv7
    rental_asset_id: UUIDv7
    asset_number_snapshot: str
    title_snapshot: str
    tariff_name: str
    tariff_type: str
    tariff_terms: str
    issued_at: datetime
    due_at: datetime | None
    grace_minutes: int


class RentalIssuePreview(PydanticBaseModel):
    """Fast confirmation context calculated from server time and item snapshots."""

    order_id: UUIDv7
    order_number: str
    customer_name_snapshot: str
    server_now: datetime
    requires_agreement: bool
    can_issue: bool
    blocked_reason: str | None
    items: list[RentalIssuePreviewItem]


class RentalOrderItemOutcome(StrEnum):
    """Terminal outcome selected by the return workflow."""

    RETURNED = "returned"
    LOST = "lost"


class RentalOrderItemCompletion(PydanticBaseModel):
    """One inspected item completed inside an atomic return workflow."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUIDv7
    outcome: RentalOrderItemOutcome
    condition: AssetCondition | None = None
    charged_amount: Decimal = Field(ge=0)
    completed_at: datetime | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_outcome_details(self) -> RentalOrderItemCompletion:
        """Require a physical condition only for items that were returned."""
        if self.outcome is RentalOrderItemOutcome.RETURNED and self.condition is None:
            raise ValueError("condition is required for a returned item")
        if self.outcome is RentalOrderItemOutcome.LOST and self.condition is not None:
            raise ValueError("condition must be omitted for a lost item")
        return self


class RentalOrderItemsComplete(PydanticBaseModel):
    """Batch of terminal item outcomes committed as one transaction."""

    model_config = ConfigDict(extra="forbid")

    items: list[RentalOrderItemCompletion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_items(self) -> RentalOrderItemsComplete:
        """Reject duplicate item commands inside one batch."""
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("each item may be completed only once")
        return self


class RentalSealVerificationDraft(PydanticBaseModel):
    """Operator verification of one seal expected from the issue snapshot."""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(min_length=1, max_length=160)
    seal_number: str = Field(min_length=1, max_length=120)
    result: RentalSealVerificationResult
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_problem_note(self) -> RentalSealVerificationDraft:
        """Require an explanation for broken, missing, or mismatched seals."""
        self.location = self.location.strip()
        self.seal_number = self.seal_number.strip()
        self.note = self.note.strip() if self.note else None
        if not self.location or not self.seal_number:
            raise ValueError("location and seal_number are required")
        if self.result is not RentalSealVerificationResult.OK and not self.note:
            raise ValueError("note is required for a seal problem")
        return self


class RentalComponentVerificationDraft(PydanticBaseModel):
    """Operator verification of one component expected from the issue snapshot."""

    model_config = ConfigDict(extra="forbid")

    component_id: UUIDv7
    name: str = Field(min_length=1, max_length=200)
    result: RentalComponentVerificationResult
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_problem_note(self) -> RentalComponentVerificationDraft:
        """Require an explanation for a missing or damaged component."""
        self.name = self.name.strip()
        self.note = self.note.strip() if self.note else None
        if not self.name:
            raise ValueError("component name is required")
        if self.result is not RentalComponentVerificationResult.PRESENT and not self.note:
            raise ValueError("note is required for a component problem")
        return self


class RentalItemReturnDraft(PydanticBaseModel):
    """Operator-entered inspection facts; timing and overdue money remain server-owned."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUIDv7
    condition: RentalReturnCondition
    damage_charge: Decimal = Field(default=Decimal("0"), max_digits=12, ge=0)
    notes: str | None = Field(default=None, max_length=4000)
    seal_verifications: list[RentalSealVerificationDraft] = Field(default_factory=list)
    component_verifications: list[RentalComponentVerificationDraft] = Field(
        default_factory=list
    )

    @field_validator("damage_charge", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        """Require exact decimal input for manual charges."""
        if isinstance(value, float):
            raise ValueError("Money must be sent as a decimal string or integer.")
        return value

    @model_validator(mode="after")
    def require_exception_notes(self) -> RentalItemReturnDraft:
        """Require an operational explanation for damage, incomplete sets, and loss."""
        self.notes = self.notes.strip() if self.notes else None
        if self.condition is not RentalReturnCondition.GOOD and not self.notes:
            raise ValueError("notes are required unless condition is good")
        return self


class RentalItemsReturn(PydanticBaseModel):
    """One atomic item-level or multi-item return command."""

    model_config = ConfigDict(extra="forbid")

    items: list[RentalItemReturnDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_items(self) -> RentalItemsReturn:
        """Prevent duplicate charges for the same item inside one request."""
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("each item may be returned only once")
        return self


class RentalReturnPreviewItem(PydanticBaseModel):
    """Server-authoritative calculation shown before confirmation."""

    item_id: UUIDv7
    asset_number_snapshot: str
    title_snapshot: str
    tariff_name: str
    tariff_terms: str
    issued_at: datetime
    due_at: datetime
    grace_minutes: int
    condition: RentalReturnCondition
    notes: str | None
    overdue_state: RentalOverdueState
    overdue_minutes: int
    overdue_blocks: int
    billing_unit_minutes: int
    billing_unit_price: Decimal
    base_rental_price: Decimal
    overdue_charge: Decimal
    damage_charge: Decimal
    total_charge: Decimal
    deposit_applied: Decimal
    asset_identification_snapshot: dict[str, object] | None
    seal_verifications: list[dict[str, object]]
    component_verifications: list[dict[str, object]]


class RentalReturnPreview(PydanticBaseModel):
    """Whole-command preview including safe order-level deposit disposition."""

    order_id: UUIDv7
    server_now: datetime
    is_partial: bool
    settlement_final_after_return: bool
    required_deposit_total: Decimal
    deposit_received: Decimal | None
    deposit_received_confirmed: bool
    deposit_applied_before: Decimal
    deposit_applied_by_return: Decimal
    provisional_deposit_remaining: Decimal | None
    deposit_to_refund: Decimal | None
    remaining_amount_due: Decimal | None
    items: list[RentalReturnPreviewItem]


class RentalOrderItemRead(PydanticBaseModel):
    """Nested RentalOrderItem representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    rental_asset_id: UUIDv7
    asset_number_snapshot: str
    title_snapshot: str
    agreed_price: Decimal
    discount: Decimal
    rental_tariff_id: UUIDv7 | None
    rental_tariff_version: int | None
    timing_source: RentalTimingSource | None
    tariff_snapshot: dict[str, str | int | bool | None] | None
    asset_identification_snapshot: dict[str, object] | None
    planned_rental_date: date | None
    planned_start_at: datetime | None
    issued_at: datetime | None
    due_at: datetime | None
    recommended_deposit: Decimal | None
    charged_amount: Decimal | None
    returned_at: datetime | None
    return_note: str | None
    return_condition: RentalReturnCondition | None
    overdue_state: RentalOverdueState | None
    overdue_minutes: int | None
    overdue_charge: Decimal | None
    damage_charge: Decimal | None
    deposit_applied: Decimal | None
    return_calculation_snapshot: dict[str, str | int | bool | None] | None
    seal_verification_snapshot: list[dict[str, object]] | None
    component_verification_snapshot: list[dict[str, object]] | None
    status: RentalOrderItemStatus

    @computed_field
    @property
    def current_overdue_state(self) -> RentalOverdueState | None:
        """Derive live urgency for an issued item without persisting a countdown."""
        if self.status is not RentalOrderItemStatus.ISSUED:
            return None
        state, _ = projected_overdue_state(
            due_at=self.due_at,
            grace_minutes=int((self.tariff_snapshot or {}).get("grace_minutes") or 0),
            now=datetime.now(UTC),
        )
        return state

    @computed_field
    @property
    def current_overdue_minutes(self) -> int | None:
        """Derive rounded-up current lateness for active workspace labels."""
        if self.status is not RentalOrderItemStatus.ISSUED:
            return None
        _, minutes = projected_overdue_state(
            due_at=self.due_at,
            grace_minutes=int((self.tariff_snapshot or {}).get("grace_minutes") or 0),
            now=datetime.now(UTC),
        )
        return minutes


class RentalOrderRead(PydanticBaseModel):
    """Complete RentalOrder aggregate representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    order_number: str
    customer_id: UUIDv7
    customer_name_snapshot: str
    customer_phone_snapshot: str
    status: RentalOrderStatus
    planned_start_at: datetime
    planned_return_at: datetime
    issued_at: datetime | None
    closed_at: datetime | None
    deposit_amount: Decimal | None
    deposit_received: Decimal | None
    deposit_received_confirmed: bool
    rental_total: Decimal
    required_deposit_total: Decimal
    recommended_deposit_total: Decimal
    requires_agreement: bool
    overdue_charge_total: Decimal
    damage_charge_total: Decimal
    deposit_applied_total: Decimal
    deposit_settlement_finalized: bool
    deposit_to_refund: Decimal | None
    remaining_amount_due: Decimal | None
    note: str | None
    items: list[RentalOrderItemRead]

    @computed_field
    @property
    def is_overdue(self) -> bool:
        """Derive overdue state without persisting another order status."""
        if self.status is not RentalOrderStatus.ISSUED:
            return False
        now = datetime.now(UTC)
        if self.planned_return_at.tzinfo is None:
            now = now.replace(tzinfo=None)
        return self.planned_return_at < now
