from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.config import get_settings
from core.customers.enums import CustomerStatus
from core.customers.repository import CustomerRepository
from core.rental.agreement_service import (
    RentalAgreementService,
    RentalAgreementTermsLockedError,
)
from core.rental.asset import RentalAsset
from core.rental.enums import (
    AssetCondition,
    RentalSealStatus,
    RentalSealVerificationResult,
)
from core.rental.exceptions import RentalDomainError
from core.rental.mapper import (
    rental_asset_from_record,
    rental_order_from_record,
    rental_order_to_record,
    update_rental_asset_record,
    update_rental_order_record,
)
from core.rental.models import RentalAssetRecord, RentalDamageRecord, RentalOrderRecord
from core.rental.order import RentalOrder, RentalOrderItem
from core.rental.order_enums import (
    RentalOrderItemStatus,
    RentalOrderStatus,
    RentalReturnCondition,
    RentalTimingSource,
)
from core.rental.order_exceptions import (
    InvalidRentalOrderItemStateError,
    InvalidRentalOrderStateError,
    RentalOrderItemNotFoundError,
)
from core.rental.order_number import RentalOrderNumberGenerator
from core.rental.order_schemas import (
    RentalDepositReceivedUpdate,
    RentalIssuePreview,
    RentalIssuePreviewItem,
    RentalItemReturnDraft,
    RentalItemsReturn,
    RentalOrderCreate,
    RentalOrderIssue,
    RentalOrderItemCompletion,
    RentalOrderItemCreate,
    RentalOrderItemOutcome,
    RentalOrderItemReturn,
    RentalOrderItemsComplete,
    RentalOrderTariffConfigure,
    RentalOrderUpdate,
    RentalReturnPreview,
    RentalReturnPreviewItem,
)
from core.rental.repository import RentalAssetRepository, RentalOrderRepository
from core.rental.return_calculator import calculate_return
from core.rental.tariff_enums import RentalTariffType
from core.rental.tariff_models import RentalTariffRecord
from core.rental.tariff_repository import RentalTariffRepository
from core.rental.tariff_service import RentalTariffService
from core.shared.db import UUIDv7, generate_uuid_v7
from core.shared.money import quantize_money


class RentalOrderNotFoundError(Exception):
    """Raised when a rental order projection does not exist."""


class RentalCustomerUnavailableError(Exception):
    """Raised when a customer cannot enter a new rental order."""


class RentalAssetNotFoundError(Exception):
    """Raised when an order references a missing rental asset."""


class RentalAssetVariantNotFoundError(Exception):
    """Raised when an asset snapshot cannot resolve its catalog variant."""


class RentalTariffUnavailableError(Exception):
    """Raised when an exact tariff cannot be selected for the planned rental date."""


class RentalTariffVariantMismatchError(Exception):
    """Raised when a tariff belongs to a different Variant than the asset."""


class RentalTimingInputError(Exception):
    """Raised when tariff-specific timing input is missing or ambiguous."""


class RentalAgreementRequiredError(Exception):
    """Raised while agreement-required issue is deferred to Sprint 4.17."""


class RentalIssueTimeError(Exception):
    """Raised when a manual actual issue timestamp is operationally implausible."""


class RentalSealVerificationError(RentalDomainError):
    """Raised when return seal observations do not match the issue snapshot."""


class RentalDepositUnconfirmedError(RentalDomainError):
    """Raised when return settlement has no confirmed actual deposit receipt."""


class RentalOrderService:
    """Application workflows coordinating RentalOrder and related aggregates."""

    def __init__(self, session: Session) -> None:
        """Create a service using one transaction-capable database session."""
        self._session = session
        self._orders = RentalOrderRepository(session)
        self._customers = CustomerRepository(session)
        self._assets = RentalAssetRepository(session)
        self._variants = CatalogVariantRepository(session)
        self._tariffs = RentalTariffRepository(session)
        self._rental_timezone = ZoneInfo(get_settings().rental_timezone)

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
        now = datetime.now(UTC)
        planned_start_at = data.planned_start_at or now
        planned_return_at = data.planned_return_at or (planned_start_at + timedelta(hours=1))
        order = RentalOrder.create(
            order_id=generate_uuid_v7(),
            order_number=RentalOrderNumberGenerator.generate(
                self._orders.reserve_next_order_number()
            ),
            customer_id=customer.id,
            customer_name_snapshot=customer.full_name,
            customer_phone_snapshot=customer.phone,
            planned_start_at=planned_start_at,
            planned_return_at=planned_return_at,
            deposit_amount=data.deposit_received,
            note=data.note,
        )
        self._orders.add(rental_order_to_record(order, actor_id=actor_id))
        self._commit()
        return order

    def add_tariff_item(
        self,
        order_id: UUIDv7,
        data: RentalOrderTariffConfigure,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Add one independently configured item to an editable order."""
        return self._configure_tariff_item(order_id, data, item_id=None, actor_id=actor_id)

    def update_tariff_item(
        self,
        order_id: UUIDv7,
        item_id: UUIDv7,
        data: RentalOrderTariffConfigure,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Replace one item's editable tariff facts without touching siblings."""
        return self._configure_tariff_item(order_id, data, item_id=item_id, actor_id=actor_id)

    def _configure_tariff_item(
        self,
        order_id: UUIDv7,
        data: RentalOrderTariffConfigure,
        *,
        item_id: UUIDv7 | None,
        actor_id: UUIDv7 | None,
    ) -> RentalOrder:
        """Resolve live references and freeze one item's contractual facts."""
        try:
            record, order = self._load_order(order_id, for_update=True)
            self._ensure_agreement_terms_editable(order_id)
            asset_record = self._assets.get_for_update(data.rental_asset_id)
            if asset_record is None:
                raise RentalAssetNotFoundError
            asset = rental_asset_from_record(asset_record)
            asset.checkout()
            variant = self._variants.get(asset.variant_id)
            if variant is None:
                raise RentalAssetVariantNotFoundError
            tariff = self._tariffs.get(data.tariff_id)
            if tariff is None:
                raise RentalTariffUnavailableError
            if tariff.variant_id != asset.variant_id:
                raise RentalTariffVariantMismatchError

            planned_date, planned_start_at, due_at = self._resolve_timing(tariff, data)
            selectable_ids = {
                candidate.id
                for candidate in RentalTariffService(self._session).list_selectable(
                    asset.variant_id, on_date=planned_date
                )
            }
            if tariff.id not in selectable_ids:
                raise RentalTariffUnavailableError

            agreed_price = quantize_money(
                data.agreed_price if data.agreed_price is not None else tariff.price
            )
            deposit_amount = quantize_money(tariff.deposit_amount or Decimal("0"))
            snapshot = self._build_tariff_snapshot(
                tariff,
                timing_source=data.timing_source,
                agreed_price=agreed_price,
                planned_rental_date=planned_date,
                planned_start_at=planned_start_at,
                due_at=due_at,
            )
            command = order.add_configured_item if item_id is None else order.update_configured_item
            command(
                item_id=item_id or generate_uuid_v7(),
                rental_asset_id=asset.id,
                asset_number_snapshot=asset.asset_number,
                title_snapshot=variant.title,
                agreed_price=agreed_price,
                planned_rental_date=planned_date,
                planned_start_at=planned_start_at,
                planned_return_at=due_at,
                deposit_amount=deposit_amount,
                rental_tariff_id=tariff.id,
                rental_tariff_version=tariff.version,
                timing_source=data.timing_source,
                tariff_snapshot=snapshot,
            )
            RentalTariffService(self._session).mark_used(tariff.id)
            update_rental_order_record(record, order, actor_id=actor_id)
            self._orders.save(record)
            self._commit()
            return order
        except Exception:
            self._session.rollback()
            raise

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
        if {"planned_start_at", "planned_return_at"} & changes.keys():
            self._ensure_agreement_terms_editable(order_id)
        if "planned_start_at" in changes or "planned_return_at" in changes:
            order.change_period(
                data.planned_start_at or order.planned_start_at,
                data.planned_return_at or order.planned_return_at,
            )
        if "deposit_received" in changes:
            order.set_deposit_received(data.deposit_received)
        if "note" in changes:
            order.update_note(data.note)
        update_rental_order_record(record, order, actor_id=actor_id)
        self._orders.save(record)
        self._commit()
        return order

    def confirm_deposit_received(
        self,
        order_id: UUIDv7,
        data: RentalDepositReceivedUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Persist the exact operator-confirmed receipt without changing contract terms."""
        record, order = self._load_order(order_id, for_update=True)
        order.set_deposit_received(data.deposit_received)
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
        self._ensure_agreement_terms_editable(order_id)
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
        self._ensure_agreement_terms_editable(order_id)
        order.remove_item(item_id)
        update_rental_order_record(record, order, actor_id=actor_id)
        self._orders.save(record)
        self._commit()
        return order

    def issue(
        self,
        order_id: UUIDv7,
        data: RentalOrderIssue | None = None,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Atomically issue an order and all of its physical RentalAssets."""
        try:
            record, order = self._load_order(order_id, for_update=True)
            if order.status is RentalOrderStatus.ISSUED:
                return order
            if order.status is not RentalOrderStatus.READY and any(
                item.rental_tariff_id is not None for item in order.items
            ):
                raise InvalidRentalOrderStateError
            if order.requires_agreement and not RentalAgreementService(
                self._session
            ).has_valid_agreement(order_id):
                raise RentalAgreementRequiredError
            actual_issue_at = self._resolve_issue_time(data.issued_at if data else None)
            asset_pairs: list[tuple[UUIDv7, RentalAssetRecord, RentalAsset]] = []
            identification_by_item: dict[UUIDv7, dict[str, object]] = {}
            for item in sorted(order.items, key=lambda candidate: str(candidate.rental_asset_id)):
                asset_record = self._assets.get_for_update(item.rental_asset_id)
                if asset_record is None:
                    raise RentalAssetNotFoundError
                asset = rental_asset_from_record(asset_record)
                asset_pairs.append((item.id, asset_record, asset))
                identification_by_item[item.id] = self._asset_identification_snapshot(
                    asset_record
                )
            for _, _, asset in asset_pairs:
                asset.checkout()
            order.issue(
                issued_at=actual_issue_at,
                identification_by_item=identification_by_item,
            )
            for _, asset_record, asset in asset_pairs:
                update_rental_asset_record(asset_record, asset)
                asset_record.updated_by_id = actor_id
            update_rental_order_record(record, order, actor_id=actor_id)
            record.issued_by_id = actor_id
            for item_record in record.items:
                item_record.issued_by_id = actor_id
            self._orders.save(record)
            self._commit()
            return order
        except Exception:
            self._session.rollback()
            raise

    def issue_preview(self, order_id: UUIDv7) -> RentalIssuePreview:
        """Project item due boundaries from one server timestamp without issuing."""
        order = self.get(order_id)
        server_now = datetime.now(UTC)
        blocked_reason: str | None = None
        if order.status is not RentalOrderStatus.READY:
            blocked_reason = "Заказ больше не ожидает выдачи."
        elif order.requires_agreement and not RentalAgreementService(
            self._session
        ).has_valid_agreement(order_id):
            blocked_reason = "Договор обязателен. Сформируйте договор перед выдачей."
        items: list[RentalIssuePreviewItem] = []
        for item in order.items:
            snapshot = item.tariff_snapshot or {}
            due_at = item.due_at_for_issue(server_now)
            if (
                blocked_reason is None
                and snapshot.get("tariff_type") == RentalTariffType.SLOT.value
                and (due_at is None or self._compare_datetime(server_now, due_at) >= 0)
            ):
                blocked_reason = "Слот уже завершён. Исправьте условия перед выдачей."
            items.append(
                RentalIssuePreviewItem(
                    id=item.id,
                    rental_asset_id=item.rental_asset_id,
                    asset_number_snapshot=item.asset_number_snapshot,
                    title_snapshot=item.title_snapshot,
                    tariff_name=str(snapshot.get("tariff_name") or "Тариф"),
                    tariff_type=str(snapshot.get("tariff_type") or "legacy"),
                    tariff_terms=str(snapshot.get("canonical_tariff_terms") or ""),
                    issued_at=server_now,
                    due_at=due_at,
                    grace_minutes=int(snapshot.get("grace_minutes") or 0),
                )
            )
        return RentalIssuePreview(
            order_id=order.id,
            order_number=order.order_number,
            customer_name_snapshot=order.customer_name_snapshot,
            server_now=server_now,
            requires_agreement=order.requires_agreement,
            can_issue=blocked_reason is None,
            blocked_reason=blocked_reason,
            items=items,
        )

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
        """Preserve the pre-tariff legacy workflow without bypassing Sprint 4.18 rules."""
        order = self.get(order_id)
        item = next((candidate for candidate in order.items if candidate.id == item_id), None)
        if item is None:
            raise RentalOrderItemNotFoundError
        if item.tariff_snapshot is not None:
            raise InvalidRentalOrderStateError
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

    def preview_return(
        self,
        order_id: UUIDv7,
        data: RentalItemsReturn,
    ) -> RentalReturnPreview:
        """Calculate item charges and deposit disposition using current server time."""
        order = self.get(order_id)
        return self._build_return_preview(order, data, returned_at=datetime.now(UTC))

    def return_items(
        self,
        order_id: UUIDv7,
        data: RentalItemsReturn,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalOrder:
        """Atomically return one or many inspected items using one server timestamp."""
        try:
            record, order = self._load_order(order_id, for_update=True)
            returned_at = datetime.now(UTC)
            preview = self._build_return_preview(order, data, returned_at=returned_at)
            if not preview.deposit_received_confirmed:
                raise RentalDepositUnconfirmedError(
                    "Фактическое получение обеспечительного платежа не было подтверждено."
                )
            commands = {command.item_id: command for command in data.items}
            preview_items = {item.item_id: item for item in preview.items}
            order_items = {item.id: item for item in order.items}
            asset_records: dict[UUIDv7, RentalAssetRecord] = {}
            assets: dict[UUIDv7, RentalAsset] = {}
            for item_id in sorted(
                commands,
                key=lambda value: str(order_items[value].rental_asset_id),
            ):
                domain_item = order_items[item_id]
                asset_record = self._assets.get_for_update(domain_item.rental_asset_id)
                if asset_record is None:
                    raise RentalAssetNotFoundError
                asset_records[item_id] = asset_record
                assets[item_id] = rental_asset_from_record(asset_record)

            for item_id, command in commands.items():
                domain_item = order_items[item_id]
                calculation = preview_items[item_id]
                asset = assets[item_id]
                calculation_snapshot = self._return_calculation_snapshot(
                    calculation, returned_at=returned_at
                )
                if command.condition is RentalReturnCondition.LOST:
                    asset.mark_lost_during_rental(command.notes or "Утрата при аренде")
                    order.mark_item_lost(
                        item_id,
                        charged_amount=calculation.total_charge,
                        completed_at=returned_at,
                        note=command.notes,
                        overdue_state=calculation.overdue_state,
                        overdue_minutes=calculation.overdue_minutes,
                        overdue_charge=calculation.overdue_charge,
                        damage_charge=calculation.damage_charge,
                        deposit_applied=calculation.deposit_applied,
                        return_calculation_snapshot=calculation_snapshot,
                        seal_verification_snapshot=calculation.seal_verifications,
                    )
                else:
                    asset_condition = (
                        AssetCondition.GOOD
                        if command.condition is RentalReturnCondition.GOOD
                        else AssetCondition.DAMAGED
                    )
                    asset.accept_return(asset_condition)
                    order.return_item(
                        item_id,
                        charged_amount=calculation.total_charge,
                        returned_at=returned_at,
                        return_note=command.notes,
                        return_condition=command.condition,
                        overdue_state=calculation.overdue_state,
                        overdue_minutes=calculation.overdue_minutes,
                        overdue_charge=calculation.overdue_charge,
                        damage_charge=calculation.damage_charge,
                        deposit_applied=calculation.deposit_applied,
                        return_calculation_snapshot=calculation_snapshot,
                        seal_verification_snapshot=calculation.seal_verifications,
                    )
                update_rental_asset_record(asset_records[item_id], asset)
                asset_records[item_id].updated_by_id = actor_id
                if command.condition in {
                    RentalReturnCondition.DAMAGED,
                    RentalReturnCondition.INCOMPLETE,
                }:
                    self._session.add(
                        RentalDamageRecord(
                            id=generate_uuid_v7(),
                            rental_asset_id=domain_item.rental_asset_id,
                            order_item_id=item_id,
                            description=command.notes or command.condition.value,
                            severity=(
                                "major"
                                if command.condition is RentalReturnCondition.DAMAGED
                                else "moderate"
                            ),
                            recorded_by_id=actor_id,
                            created_by_id=actor_id,
                        )
                    )

            update_rental_order_record(record, order, actor_id=actor_id)
            for item_record in record.items:
                if item_record.id in commands:
                    item_record.completed_by_id = actor_id
            self._orders.save(record)
            self._commit()
            return order
        except Exception:
            self._session.rollback()
            raise

    def _build_return_preview(
        self,
        order: RentalOrder,
        data: RentalItemsReturn,
        *,
        returned_at: datetime,
    ) -> RentalReturnPreview:
        """Build one deterministic preview shared by confirmation and mutation."""
        if order.status is not RentalOrderStatus.ISSUED:
            raise InvalidRentalOrderStateError
        order_items = {item.id: item for item in order.items}
        active_ids = {
            item.id for item in order.items if item.status is RentalOrderItemStatus.ISSUED
        }
        requested_ids = {command.item_id for command in data.items}
        if not requested_ids <= active_ids:
            missing = requested_ids - set(order_items)
            if missing:
                raise RentalOrderItemNotFoundError
            raise InvalidRentalOrderItemStateError

        deposit_received = order.deposit_received
        available_deposit = deposit_received or Decimal("0")
        remaining_deposit = max(
            available_deposit - order.deposit_applied_total,
            Decimal("0"),
        )
        preview_items: list[RentalReturnPreviewItem] = []
        deposit_applied_by_return = Decimal("0")
        new_liabilities = Decimal("0")
        for command in data.items:
            item = order_items[command.item_id]
            if item.due_at is None or item.issued_at is None or item.tariff_snapshot is None:
                raise RentalTimingInputError
            calculation = calculate_return(
                tariff_snapshot=item.tariff_snapshot,
                agreed_price=item.agreed_price,
                discount=item.discount,
                due_at=item.due_at,
                returned_at=returned_at,
                damage_charge=command.damage_charge,
            )
            liabilities = calculation.overdue_charge + calculation.damage_charge
            deposit_applied = min(remaining_deposit, liabilities)
            remaining_deposit = quantize_money(remaining_deposit - deposit_applied)
            deposit_applied_by_return += deposit_applied
            new_liabilities += liabilities
            snapshot = item.tariff_snapshot
            seal_verifications = self._normalized_seal_verifications(item, command)
            preview_items.append(
                RentalReturnPreviewItem(
                    item_id=item.id,
                    asset_number_snapshot=item.asset_number_snapshot,
                    title_snapshot=item.title_snapshot,
                    tariff_name=str(snapshot.get("tariff_name") or "Тариф"),
                    tariff_terms=str(snapshot.get("canonical_tariff_terms") or ""),
                    issued_at=item.issued_at,
                    due_at=item.due_at,
                    grace_minutes=calculation.rule.grace_minutes,
                    condition=command.condition,
                    notes=command.notes,
                    overdue_state=calculation.overdue_state,
                    overdue_minutes=calculation.overdue_minutes,
                    overdue_blocks=calculation.overdue_blocks,
                    billing_unit_minutes=calculation.rule.unit_minutes,
                    billing_unit_price=calculation.rule.unit_price,
                    base_rental_price=calculation.base_rental_price,
                    overdue_charge=calculation.overdue_charge,
                    damage_charge=calculation.damage_charge,
                    total_charge=calculation.total_charge,
                    deposit_applied=deposit_applied,
                    asset_identification_snapshot=item.asset_identification_snapshot,
                    seal_verifications=seal_verifications,
                )
            )

        final_after_return = requested_ids == active_ids
        applied_total = order.deposit_applied_total + deposit_applied_by_return
        liabilities_total = order.overdue_charge_total + order.damage_charge_total + new_liabilities
        return RentalReturnPreview(
            order_id=order.id,
            server_now=returned_at,
            is_partial=not final_after_return,
            settlement_final_after_return=final_after_return,
            required_deposit_total=order.required_deposit_total,
            deposit_received=deposit_received,
            deposit_received_confirmed=order.deposit_received_confirmed,
            deposit_applied_before=order.deposit_applied_total,
            deposit_applied_by_return=quantize_money(deposit_applied_by_return),
            provisional_deposit_remaining=(
                remaining_deposit if order.deposit_received_confirmed else None
            ),
            deposit_to_refund=(
                remaining_deposit
                if final_after_return and order.deposit_received_confirmed
                else None
            ),
            remaining_amount_due=(
                quantize_money(max(liabilities_total - applied_total, Decimal("0")))
                if final_after_return and order.deposit_received_confirmed
                else None
            ),
            items=preview_items,
        )

    @staticmethod
    def _return_calculation_snapshot(
        item: RentalReturnPreviewItem,
        *,
        returned_at: datetime,
    ) -> dict[str, str | int | bool | None]:
        """Serialize the exact rule and amounts so history never needs recalculation."""
        return {
            "overdue_policy": "next_period",
            "grace_minutes": item.grace_minutes,
            "billing_unit_minutes": item.billing_unit_minutes,
            "billing_unit_price": f"{item.billing_unit_price:.2f}",
            "overdue_blocks": item.overdue_blocks,
            "base_rental_price": f"{item.base_rental_price:.2f}",
            "overdue_charge": f"{item.overdue_charge:.2f}",
            "damage_charge": f"{item.damage_charge:.2f}",
            "total_charge": f"{item.total_charge:.2f}",
            "deposit_applied": f"{item.deposit_applied:.2f}",
            "due_at": item.due_at.isoformat(),
            "returned_at": returned_at.isoformat(),
        }

    @staticmethod
    def _asset_identification_snapshot(record: RentalAssetRecord) -> dict[str, object]:
        """Freeze current optional serial and active seals for one physical issue."""
        active_seals = sorted(
            (seal for seal in record.seals if seal.status is RentalSealStatus.ACTIVE),
            key=lambda seal: (seal.location.casefold(), seal.seal_number.casefold()),
        )
        return {
            "asset_code": record.asset_number,
            "serial_number": record.serial_number,
            "seals": [
                {"location": seal.location, "seal_number": seal.seal_number}
                for seal in active_seals
            ],
        }

    @staticmethod
    def _normalized_seal_verifications(
        item: RentalOrderItem,
        command: RentalItemReturnDraft,
    ) -> list[dict[str, object]]:
        """Validate return observations against the immutable issue snapshot."""
        identification = item.asset_identification_snapshot or {}
        raw_expected = identification.get("seals") or []
        expected = {
            (str(value.get("location")), str(value.get("seal_number")))
            for value in raw_expected
            if isinstance(value, dict) and value.get("seal_number") and value.get("location")
        }
        submitted = command.seal_verifications
        if command.condition is RentalReturnCondition.LOST and expected and not submitted:
            return [
                {
                    "location": location,
                    "expected_seal_number": seal_number,
                    "result": RentalSealVerificationResult.MISSING.value,
                    "note": command.notes,
                }
                for location, seal_number in sorted(expected)
            ]
        submitted_by_seal = {(value.location, value.seal_number): value for value in submitted}
        if len(submitted_by_seal) != len(submitted) or set(submitted_by_seal) != expected:
            raise RentalSealVerificationError(
                "Проверьте каждую пломбу из снимка выдачи; лишние номера недопустимы."
            )
        return [
            {
                "location": location,
                "expected_seal_number": seal_number,
                "result": submitted_by_seal[(location, seal_number)].result.value,
                "note": submitted_by_seal[(location, seal_number)].note,
            }
            for location, seal_number in sorted(expected)
        ]

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
            if any(
                order_items.get(completion.item_id) is not None
                and order_items[completion.item_id].tariff_snapshot is not None
                for completion in data.items
            ):
                raise InvalidRentalOrderStateError
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

    def _ensure_agreement_terms_editable(self, order_id: UUIDv7) -> None:
        """Lock agreement-relevant terms once their immutable PDF exists."""
        if RentalAgreementService(self._session).has_valid_agreement(order_id):
            raise RentalAgreementTermsLockedError

    def _resolve_timing(
        self,
        tariff: RentalTariffRecord,
        data: RentalOrderTariffConfigure,
    ) -> tuple[date, datetime | None, datetime | None]:
        """Resolve draft timing without inventing a DURATION issue timestamp."""
        if data.timing_source is RentalTimingSource.MANUAL:
            if data.planned_start_at is None or data.planned_return_at is None:
                raise RentalTimingInputError
            start = self._require_aware(data.planned_start_at)
            due = self._require_aware(data.planned_return_at)
            if due <= start:
                raise RentalTimingInputError
            return start.astimezone(self._rental_timezone).date(), start, due

        if tariff.type is RentalTariffType.DURATION:
            planned_date = data.rental_date
            if planned_date is None and data.planned_start_at is not None:
                planned_date = (
                    self._require_aware(data.planned_start_at)
                    .astimezone(self._rental_timezone)
                    .date()
                )
            if planned_date is None or tariff.duration_minutes is None:
                raise RentalTimingInputError
            return planned_date, None, None

        if data.rental_date is None or tariff.slot_start is None or tariff.slot_end is None:
            raise RentalTimingInputError
        start = datetime.combine(data.rental_date, tariff.slot_start, self._rental_timezone)
        due = datetime.combine(data.rental_date, tariff.slot_end, self._rental_timezone)
        return data.rental_date, start, due

    @staticmethod
    def _require_aware(value: datetime) -> datetime:
        """Reject browser-local datetimes without an explicit UTC offset."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise RentalTimingInputError
        return value

    @staticmethod
    def _build_tariff_snapshot(
        tariff: RentalTariffRecord,
        *,
        timing_source: RentalTimingSource,
        agreed_price: Decimal,
        planned_rental_date: date,
        planned_start_at: datetime | None,
        due_at: datetime | None,
    ) -> dict[str, str | int | bool | None]:
        """Freeze complete commercial and timing terms independently of live tariff data."""
        tariff_price = quantize_money(tariff.price)
        overdue_unit_minutes = tariff.duration_minutes
        if overdue_unit_minutes is None and tariff.slot_start and tariff.slot_end:
            start_minutes = tariff.slot_start.hour * 60 + tariff.slot_start.minute
            end_minutes = tariff.slot_end.hour * 60 + tariff.slot_end.minute
            overdue_unit_minutes = end_minutes - start_minutes
        return {
            "tariff_id": str(tariff.id),
            "tariff_lineage_id": str(tariff.lineage_id),
            "tariff_version": tariff.version,
            "tariff_name": tariff.name,
            "tariff_type": tariff.type.value,
            "tariff_price": f"{tariff_price:.2f}",
            "agreed_price": f"{agreed_price:.2f}",
            "price_overridden": agreed_price != tariff_price,
            "duration_minutes": tariff.duration_minutes,
            "slot_start": tariff.slot_start.strftime("%H:%M") if tariff.slot_start else None,
            "slot_end": tariff.slot_end.strftime("%H:%M") if tariff.slot_end else None,
            "grace_minutes": tariff.grace_minutes,
            "overdue_policy": "next_period",
            "overdue_unit_minutes": overdue_unit_minutes,
            "overdue_unit_price": f"{agreed_price:.2f}",
            "deposit_amount": (
                f"{quantize_money(tariff.deposit_amount):.2f}"
                if tariff.deposit_amount is not None
                else None
            ),
            "requires_agreement": tariff.requires_agreement,
            "canonical_tariff_terms": tariff.format_terms(),
            "timing_source": timing_source.value,
            "planned_rental_date": planned_rental_date.isoformat(),
            "planned_start_at": (
                planned_start_at.isoformat() if planned_start_at is not None else None
            ),
            "due_at": due_at.isoformat() if due_at is not None else None,
        }

    @staticmethod
    def _resolve_issue_time(override: datetime | None) -> datetime:
        """Use server time by default and constrain exceptional backdating."""
        now = datetime.now(UTC)
        if override is None:
            return now
        actual = override.astimezone(UTC)
        if actual > now + timedelta(minutes=1) or actual < now - timedelta(hours=24):
            raise RentalIssueTimeError
        return actual

    @staticmethod
    def _compare_datetime(left: datetime, right: datetime) -> int:
        """Compare aware values while tolerating SQLite's timezone loss in tests."""
        normalized_left = left.replace(tzinfo=UTC) if left.tzinfo is None else left.astimezone(UTC)
        normalized_right = (
            right.replace(tzinfo=UTC) if right.tzinfo is None else right.astimezone(UTC)
        )
        return (normalized_left > normalized_right) - (normalized_left < normalized_right)

    def _commit(self) -> None:
        """Commit one completed workflow and restore session usability on failure."""
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
