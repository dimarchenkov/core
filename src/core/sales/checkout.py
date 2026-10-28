from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter

from sqlalchemy.orm import Session

from core.integrations.enums import IntegrationProvider
from core.inventory.service import InventoryService
from core.sales.enums import (
    CheckoutPaymentOption,
    FinancialActorType,
    FinancialEntityType,
    FinancialEventType,
    FiscalizationJobStatus,
    FiscalizationStatus,
    PaymentJobStatus,
    PaymentMethod,
    PaymentStatus,
    SaleStatus,
)
from core.sales.financial_events import append_financial_event
from core.sales.models import (
    Fiscalization,
    FiscalizationJob,
    PaymentAttempt,
    PaymentJob,
    Sale,
)
from core.sales.providers import (
    CheckoutProviderBinding,
    FiscalLine,
    FiscalProvider,
    FiscalRequest,
    PaymentProvider,
    PaymentRequest,
    ProviderCallError,
    ProviderErrorKind,
    ProviderOperation,
    ProviderOutcomeState,
)
from core.sales.repository import SaleRepository
from core.sales.service import SaleNotFoundError
from core.shared.db import UUIDv7

logger = logging.getLogger(__name__)


class CheckoutStateError(Exception):
    """Raised when checkout is incompatible with the current Sale lifecycle."""


class CheckoutEmptySaleError(Exception):
    """Raised when an empty cart is submitted for payment."""


class CheckoutProviderMismatchError(Exception):
    """Raised when recovery resolves a different Integration than the persisted one."""


@dataclass(frozen=True)
class CheckoutStart:
    """Internal result distinguishing an idempotent replay from a new provider call."""

    sale_id: UUIDv7
    attempt_id: UUIDv7
    request: PaymentRequest
    initiate: bool


class CheckoutService:
    """Durable payment, inventory and fiscalization orchestration for Sales."""

    def __init__(
        self,
        session: Session,
        binding: CheckoutProviderBinding | None,
        *,
        actor_type: FinancialActorType = FinancialActorType.OPERATOR,
    ) -> None:
        """Create a checkout service with generic provider capabilities."""
        self._session = session
        self._binding = binding
        self._actor_type = actor_type
        self._sales = SaleRepository(session)
        self._inventory = InventoryService(session)

    def start(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        payment_option: CheckoutPaymentOption = CheckoutPaymentOption.CARD,
    ) -> Sale:
        """Freeze one DRAFT and persist one card or cash payment fact."""
        start = self._prepare_payment(
            sale_id,
            owner_id=owner_id,
            retry=False,
            payment_option=payment_option,
        )
        if start.request.method is PaymentMethod.CASH:
            return self._advance_paid_sale(start.sale_id, owner_id=owner_id)
        return self._require_owned(sale_id, owner_id)

    def retry_payment(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        payment_option: CheckoutPaymentOption = CheckoutPaymentOption.CARD,
    ) -> Sale:
        """Create a new attempt only after the prior result is failed or canceled."""
        start = self._prepare_payment(
            sale_id,
            owner_id=owner_id,
            retry=True,
            payment_option=payment_option,
        )
        if start.request.method is PaymentMethod.CASH:
            return self._advance_paid_sale(start.sale_id, owner_id=owner_id)
        return self._require_owned(sale_id, owner_id)

    def confirm_external_pos_payment(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        risk_acknowledged: bool,
        operator_note: str | None = None,
    ) -> Sale:
        """Complete the original Sale after operator-reported payment/receipt on the POS.

        The original acquiring attempt remains a provider fact and may still be
        reconciled.  A separate cash-shaped PaymentAttempt is the effective
        accounting fact, while fiscalization is explicitly skipped only because
        the operator confirmed that the physical POS already issued the receipt.
        """
        if not risk_acknowledged:
            raise CheckoutStateError("Необходимо подтвердить риск незавершённой операции CORE.")
        sale = self._locked_owned(sale_id, owner_id)
        existing = self._emergency_fallback_payment(sale)
        if existing is not None:
            if sale.status is SaleStatus.COMPLETED:
                self._commit()
                return sale
            self._rollback()
            raise CheckoutStateError(
                "Аварийное подтверждение уже записано, но продажа не завершена."
            )
        if any(
            item.status is PaymentStatus.SUCCEEDED and not item.accounting_superseded
            for item in sale.payments
        ):
            self._rollback()
            raise CheckoutStateError(
                "Оплата уже подтверждена в CORE. Аварийное подтверждение не записано."
            )
        if sale.status is not SaleStatus.PAYMENT_PENDING:
            self._rollback()
            raise CheckoutStateError(
                "Оплачено на кассе доступно только для незавершённой оплаты."
            )
        original = self._latest_payment(sale)
        if (
            original.payment_method is not PaymentMethod.CARD
            or original.status not in {PaymentStatus.PENDING, PaymentStatus.UNKNOWN}
        ):
            self._rollback()
            raise CheckoutStateError("Исходная операция не допускает аварийное завершение.")
        if sale.fiscalization is not None or sale.fiscalization_job is not None:
            self._rollback()
            raise CheckoutStateError(
                "Формирование чека уже началось. Требуется отдельная ручная сверка."
            )
        correlation_id = self._payment_correlation(original)
        self._event(
            sale=sale,
            entity_type=FinancialEntityType.RECONCILIATION,
            entity_id=original.id,
            event_type=FinancialEventType.EMERGENCY_FALLBACK_REQUESTED,
            correlation_id=correlation_id,
            actor_id=owner_id,
            previous_status=original.status.value,
            new_status=original.status.value,
            provider=original.provider,
            provider_operation_id=original.external_id,
            metadata={"risk_acknowledged": True, "source": "EXTERNAL_POS"},
        )
        now = datetime.now(UTC)
        fallback = PaymentAttempt(
            sale_id=sale.id,
            integration_id=None,
            provider=None,
            payment_method=PaymentMethod.CASH,
            requested_amount=sale.total_amount,
            fiscalization_required=False,
            currency=sale.currency,
            status=PaymentStatus.SUCCEEDED,
            idempotency_key=f"sale:{sale.id}:emergency-fallback",
            attempt_number=max(item.attempt_number for item in sale.payments) + 1,
            provider_metadata={
                "source": "EXTERNAL_POS",
                "confirmation": "OPERATOR_CONFIRMED",
                "confirmed_by_operator": True,
                "risk_acknowledged": True,
                "confirmed_at": now.isoformat(),
                "linked_payment_attempt_id": str(original.id),
                "operator_note": operator_note.strip() if operator_note else None,
            },
            created_by_id=owner_id,
            updated_by_id=owner_id,
        )
        sale.payments.append(fallback)
        self._sales.add_payment(fallback)
        self._session.flush()
        original.provider_metadata = {
            **original.provider_metadata,
            "accounting_superseded": True,
            "emergency_fallback_linked": True,
            "effective_payment_attempt_id": str(fallback.id),
        }
        original.updated_by_id = owner_id
        if original.job is not None:
            if (
                original.external_id is None
                and original.job.status is not PaymentJobStatus.PROCESSING
            ):
                original.job.status = PaymentJobStatus.FAILED
                original.job.next_retry_at = None
                original.job.completed_at = None
                original.job.last_error = (
                    "Emergency fallback recorded; payment command cannot be dispatched again."
                )
                original.job.updated_by_id = owner_id
            elif (
                original.external_id is not None
                and original.job.status is not PaymentJobStatus.PROCESSING
            ):
                original.job.status = PaymentJobStatus.QUEUED
                original.job.next_retry_at = now
                original.job.completed_at = None
                original.job.last_error = None
                original.job.reconciliation_requested_at = now
                original.job.updated_by_id = owner_id
        sale.status = SaleStatus.PAID
        sale.paid_at = now
        sale.updated_by_id = owner_id
        self._event(
            sale=sale,
            entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
            entity_id=fallback.id,
            event_type=FinancialEventType.EMERGENCY_FALLBACK_CONFIRMED,
            correlation_id=correlation_id,
            actor_id=owner_id,
            previous_status=original.status.value,
            new_status=PaymentStatus.SUCCEEDED.value,
            safe_message="Оператор сообщил об оплате и чеке на физической кассе.",
            metadata={
                "risk_acknowledged": True,
                "source": "EXTERNAL_POS",
                "linked_payment_attempt_id": str(original.id),
            },
        )
        self._event(
            sale=sale,
            entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
            entity_id=fallback.id,
            event_type=FinancialEventType.EXTERNAL_PAYMENT_RECORDED,
            correlation_id=correlation_id,
            actor_id=owner_id,
            new_status=PaymentStatus.SUCCEEDED.value,
            safe_message="Оплачено на кассе вручную.",
            metadata={
                "source": "EXTERNAL_POS",
                "confirmation": "OPERATOR_CONFIRMED",
                "operator_note": operator_note.strip() if operator_note else None,
            },
        )
        self._event(
            sale=sale,
            entity_type=FinancialEntityType.RECONCILIATION,
            entity_id=original.id,
            event_type=FinancialEventType.MANUAL_REVIEW_REQUIRED,
            correlation_id=correlation_id,
            actor_id=owner_id,
            previous_status=original.status.value,
            new_status=original.status.value,
            provider=original.provider,
            provider_operation_id=original.external_id,
            safe_message=(
                "Исходная операция провайдера остаётся на сверке; возможное списание "
                "нужно проверить."
            ),
        )
        self._stage_paid_sale(sale, fallback, owner_id=owner_id)
        self._commit()
        return sale

    def schedule_payment_cancellation(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
    ) -> tuple[Sale, bool]:
        """Persist one active-payment cancellation intent for the durable worker."""
        sale = self._locked_owned(sale_id, owner_id)
        if sale.status is not SaleStatus.PAYMENT_PENDING:
            self._rollback()
            raise CheckoutStateError("Отмена доступна только для активной оплаты.")
        attempt = self._latest_payment(sale)
        if (
            attempt.payment_method is not PaymentMethod.CARD
            or attempt.status not in {PaymentStatus.PENDING, PaymentStatus.UNKNOWN}
            or not attempt.external_id
        ):
            self._rollback()
            raise CheckoutStateError(
                "Для отмены нужна незавершённая операция с идентификатором провайдера."
            )
        if attempt.cancellation_requested:
            self._commit()
            return sale, False
        job = attempt.job
        if job is None:
            self._rollback()
            raise CheckoutStateError("Задание проверки оплаты отсутствует.")
        now = datetime.now(UTC)
        attempt.provider_metadata = {
            **attempt.provider_metadata,
            "cancellation_intent": {
                "state": "requested",
                "requested_at": now.isoformat(),
                "requested_by": str(owner_id),
            },
        }
        attempt.updated_by_id = owner_id
        if job.status is not PaymentJobStatus.PROCESSING:
            job.status = PaymentJobStatus.QUEUED
            job.next_retry_at = now
            job.completed_at = None
            job.last_error = None
            job.reconciliation_requested_at = now
            job.updated_by_id = owner_id
        self._event(
            sale=sale,
            entity_type=FinancialEntityType.RECONCILIATION,
            entity_id=attempt.id,
            event_type=FinancialEventType.RECONCILIATION_REQUESTED,
            correlation_id=self._payment_correlation(attempt),
            actor_id=owner_id,
            previous_status=attempt.status.value,
            new_status=attempt.status.value,
            provider=attempt.provider,
            provider_operation_id=attempt.external_id,
            safe_message=(
                "Оператор запросил отмену. CORE сначала проверит фактический статус "
                "операции у провайдера."
            ),
            metadata={"reason": "operator_cancellation_requested"},
        )
        self._commit()
        return sale, True

    def request_pending_payment_cancellation(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        attempt_id: UUIDv7,
    ) -> Sale:
        """Request one safe device cancellation after a read-only status check.

        The request is durable and never treated as evidence that a bank charge
        was canceled.  A later provider status query remains authoritative.
        """
        sale = self._locked_owned(sale_id, owner_id)
        attempt = self._payment_by_id(sale, attempt_id)
        fallback = self._emergency_fallback_payment(sale)
        operator_requested = attempt.cancellation_requested
        if (
            not (
                operator_requested
                or (fallback is not None and attempt.accounting_superseded)
            )
            or attempt.status is not PaymentStatus.PENDING
            or not attempt.external_id
        ):
            self._commit()
            return sale
        cancellation = attempt.provider_metadata.get(
            "device_operation_cancellation"
        ) or attempt.provider_metadata.get("emergency_cancellation")
        if isinstance(cancellation, dict) and cancellation.get("state") in {
            "requested",
            "unknown",
            "failed",
        }:
            self._commit()
            return sale
        now = datetime.now(UTC)
        try:
            self._require_payment_provider().request_payment_cancellation(
                attempt.external_id,
                self._payment_request(sale, attempt),
            )
        except ProviderCallError as exc:
            previous_status = attempt.status
            attempt.provider_metadata = {
                **attempt.provider_metadata,
                "device_operation_cancellation": {
                    "state": "unknown" if exc.outcome_unknown else "failed",
                    "requested_at": now.isoformat(),
                    "error_code": exc.code,
                },
            }
            if exc.outcome_unknown:
                attempt.status = PaymentStatus.UNKNOWN
                if fallback is None:
                    sale.status = SaleStatus.PAYMENT_PENDING
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.RECONCILIATION,
                entity_id=attempt.id,
                event_type=FinancialEventType.RECONCILIATION_INCONCLUSIVE,
                correlation_id=self._payment_correlation(attempt),
                actor_id=owner_id,
                previous_status=previous_status.value,
                new_status=attempt.status.value,
                provider=attempt.provider,
                provider_operation_id=attempt.external_id,
                error_code=exc.code,
                safe_message=(
                    "Результат запроса отмены команды неизвестен. CORE продолжит "
                    "только безопасную проверку статуса."
                ),
            )
        else:
            attempt.provider_metadata = {
                **attempt.provider_metadata,
                "device_operation_cancellation": {
                    "state": "requested",
                    "requested_at": now.isoformat(),
                },
            }
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.RECONCILIATION,
                entity_id=attempt.id,
                event_type=FinancialEventType.RECONCILIATION_REQUESTED,
                correlation_id=self._payment_correlation(attempt),
                actor_id=owner_id,
                previous_status=attempt.status.value,
                new_status=attempt.status.value,
                provider=attempt.provider,
                provider_operation_id=attempt.external_id,
                safe_message=(
                    "Запрошена отмена команды на устройстве. Банковское списание "
                    "считается отменённым только после финального статуса провайдера."
                ),
            )
        attempt.updated_by_id = owner_id
        self._commit()
        return sale

    def progress(self, sale_id: UUIDv7, *, owner_id: UUIDv7) -> Sale:
        """Recover provider state and advance only safe, persisted lifecycle transitions."""
        sale = self._require_owned(sale_id, owner_id)
        self._ensure_binding_matches(sale)
        # Acquiring and fiscalization are worker-owned. HTTP only observes state.
        if sale.status is SaleStatus.PAYMENT_PENDING:
            return sale
        if sale.status is SaleStatus.PAID:
            return self._advance_paid_sale(sale.id, owner_id=owner_id)
        # Fiscalization is owned by the durable server-side worker. HTTP callers
        # only observe the persisted state and must never drive the provider poll.
        if sale.status is SaleStatus.FISCALIZATION_PENDING:
            return sale
        return sale

    def retry_fiscalization(self, sale_id: UUIDv7, *, owner_id: UUIDv7) -> Sale:
        """Requeue a definitely failed receipt without running provider work in HTTP."""
        sale = self._locked_owned(sale_id, owner_id)
        self._ensure_binding_matches(sale)
        fiscalization = sale.fiscalization
        if fiscalization is not None and fiscalization.operator_dismissed:
            self._rollback()
            raise CheckoutStateError("Операция отмечена оператором как неактуальная.")
        if sale.status is not SaleStatus.FISCALIZATION_FAILED or fiscalization is None:
            self._rollback()
            raise CheckoutStateError("Fiscalization retry requires a definite failed receipt.")
        previous_ids = list(fiscalization.provider_metadata.get("previous_external_ids", []))
        if fiscalization.external_id:
            previous_ids.append(fiscalization.external_id)
        fiscalization.provider_metadata = {
            **fiscalization.provider_metadata,
            "previous_external_ids": previous_ids,
        }
        fiscalization.external_id = None
        fiscalization.status = FiscalizationStatus.PENDING
        fiscalization.attempt_count += 1
        fiscalization.error_code = None
        fiscalization.error_message = None
        job = sale.fiscalization_job
        if job is None:
            self._rollback()
            raise CheckoutStateError("Fiscalization job is missing.")
        job.status = FiscalizationJobStatus.QUEUED
        job.correlation_id = f"fiscal:{fiscalization.id}:attempt:{fiscalization.attempt_count}"
        job.dispatch_started_at = None
        job.next_retry_at = datetime.now(UTC)
        job.last_error = None
        job.completed_at = None
        job.updated_by_id = owner_id
        sale.status = SaleStatus.FISCALIZATION_PENDING
        sale.updated_by_id = owner_id
        self._event(
            sale=sale,
            entity_type=FinancialEntityType.FISCALIZATION_JOB,
            entity_id=job.id,
            event_type=FinancialEventType.FISCAL_COMMAND_PREPARED,
            correlation_id=job.correlation_id,
            actor_id=owner_id,
            previous_status=FiscalizationJobStatus.FAILED.value,
            new_status=FiscalizationJobStatus.QUEUED.value,
            provider=fiscalization.provider,
        )
        self._commit()
        return sale

    def dismiss_failed_fiscalization(
        self, sale_id: UUIDv7, *, owner_id: UUIDv7, reason: str
    ) -> Sale:
        """Dismiss an inactive failed receipt operationally, preserving all financial facts."""
        sale = self._locked_owned(sale_id, owner_id)
        fiscal = sale.fiscalization
        if fiscal is not None and fiscal.operator_dismissed:
            self._commit()
            return sale
        job = sale.fiscalization_job
        if (
            sale.status is not SaleStatus.FISCALIZATION_FAILED
            or fiscal is None
            or fiscal.status is not FiscalizationStatus.FAILED
            or (job is not None and job.status is not FiscalizationJobStatus.FAILED)
            or not reason.strip()
        ):
            self._rollback()
            raise CheckoutStateError("Only an inactive failed fiscalization can be dismissed.")
        self._successful_payment(sale)
        fiscal.provider_metadata = {
            **fiscal.provider_metadata,
            "operator_dismissed": True,
            "operator_dismissal": {
                "actor_id": str(owner_id),
                "at": datetime.now(UTC).isoformat(),
                "reason": reason.strip(),
                "sale_status": sale.status.value,
                "fiscal_status": fiscal.status.value,
                "job_status": job.status.value if job else None,
                "previous_next_retry_at": str(job.next_retry_at) if job else None,
            },
        }
        fiscal.updated_by_id = owner_id
        fiscal.version += 1
        if job is not None:
            job.next_retry_at = None
            job.updated_by_id = owner_id
        self._commit()
        return sale

    def _prepare_payment(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        retry: bool,
        payment_option: CheckoutPaymentOption,
    ) -> CheckoutStart:
        sale = self._locked_owned(sale_id, owner_id)
        payment_method, fiscalization_required = self._payment_facts(payment_option)
        if not retry and sale.status in {
            SaleStatus.PAID,
            SaleStatus.FISCALIZATION_PENDING,
            SaleStatus.FISCALIZATION_FAILED,
            SaleStatus.COMPLETED,
        }:
            successful = self._successful_payment(sale)
            if (
                successful.payment_method is PaymentMethod.CASH
                and payment_method is PaymentMethod.CASH
                and successful.fiscalization_required is fiscalization_required
            ):
                self._ensure_binding_matches(sale)
                request = self._payment_request(sale, successful)
                self._commit()
                return CheckoutStart(sale.id, successful.id, request, False)
            self._rollback()
            raise CheckoutStateError(f"Sale cannot start payment from {sale.status.value}.")
        if sale.status is SaleStatus.PAYMENT_PENDING and not retry:
            if payment_option is not CheckoutPaymentOption.CARD:
                self._rollback()
                raise CheckoutStateError("Pending acquiring payment cannot be replaced with cash.")
            self._ensure_binding_matches(sale)
            attempt = self._latest_payment(sale)
            request = self._payment_request(sale, attempt)
            self._commit()
            return CheckoutStart(sale.id, attempt.id, request, False)
        if sale.status is not SaleStatus.DRAFT:
            self._rollback()
            raise CheckoutStateError(f"Sale cannot start payment from {sale.status.value}.")
        if sale.payments and not retry:
            self._rollback()
            raise CheckoutStateError(
                "A prior payment attempt exists; use an explicit safe payment retry."
            )
        if retry and (
            not sale.payments
            or self._latest_payment(sale).status
            not in {PaymentStatus.FAILED, PaymentStatus.CANCELED}
        ):
            self._rollback()
            raise CheckoutStateError(
                "Payment retry requires a definite failed or canceled attempt."
            )
        if not sale.items or sale.total_amount <= 0:
            self._rollback()
            raise CheckoutEmptySaleError
        if payment_method is PaymentMethod.CARD and (
            self._binding is None or self._binding.payment is None
        ):
            self._rollback()
            raise CheckoutStateError("Payment provider is required for card checkout.")
        if fiscalization_required and (
            self._binding is None or self._binding.fiscal is None
        ):
            self._rollback()
            raise CheckoutStateError("Fiscal provider is required for this payment option.")
        binding = self._binding
        attempt_number = len(sale.payments) + 1
        attempt = PaymentAttempt(
            sale_id=sale.id,
            integration_id=(
                binding.integration_id if payment_method is PaymentMethod.CARD and binding else None
            ),
            provider=binding.provider if payment_method is PaymentMethod.CARD and binding else None,
            payment_method=payment_method,
            requested_amount=sale.total_amount,
            fiscalization_required=fiscalization_required,
            currency=sale.currency,
            status=(
                PaymentStatus.SUCCEEDED
                if payment_method is PaymentMethod.CASH
                else PaymentStatus.PENDING
            ),
            idempotency_key=f"sale:{sale.id}:payment:{attempt_number}",
            attempt_number=attempt_number,
            provider_metadata=self._initial_payment_metadata(
                payment_method=payment_method,
                fiscalization_required=fiscalization_required,
                binding=binding,
            ),
            created_by_id=owner_id,
            updated_by_id=owner_id,
        )
        sale.payments.append(attempt)
        self._sales.add_payment(attempt)
        if payment_method is PaymentMethod.CASH:
            sale.status = SaleStatus.PAID
            sale.paid_at = datetime.now(UTC)
        else:
            sale.status = SaleStatus.PAYMENT_PENDING
        sale.updated_by_id = owner_id
        self._session.flush()
        if payment_method is PaymentMethod.CARD:
            job = PaymentJob(
                sale_id=sale.id,
                payment_attempt_id=attempt.id,
                status=PaymentJobStatus.QUEUED,
                attempt_count=0,
                next_retry_at=datetime.now(UTC),
                correlation_id=f"payment:{attempt.id}",
                created_by_id=owner_id,
                updated_by_id=owner_id,
            )
            attempt.job = job
            sale.payment_jobs.append(job)
            self._sales.add_payment_job(job)
            self._session.flush()
        payment_correlation = self._payment_correlation(attempt)
        self._event(
            sale=sale,
            entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
            entity_id=attempt.id,
            event_type=FinancialEventType.PAYMENT_CREATED,
            correlation_id=payment_correlation,
            actor_id=owner_id,
            new_status=attempt.status.value,
            provider=attempt.provider,
            metadata={"payment_method": attempt.payment_method.value},
        )
        if payment_method is PaymentMethod.CARD:
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.PAYMENT_JOB,
                entity_id=attempt.job.id,
                event_type=FinancialEventType.PAYMENT_COMMAND_PREPARED,
                correlation_id=payment_correlation,
                actor_id=owner_id,
                new_status=PaymentJobStatus.QUEUED.value,
                provider=attempt.provider,
            )
        else:
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
                entity_id=attempt.id,
                event_type=FinancialEventType.PAYMENT_APPROVED,
                correlation_id=payment_correlation,
                actor_id=owner_id,
                previous_status=PaymentStatus.PENDING.value,
                new_status=PaymentStatus.SUCCEEDED.value,
                provider=None,
                safe_message="Наличные подтверждены оператором.",
            )
        request = self._payment_request(sale, attempt)
        if attempt.status is PaymentStatus.SUCCEEDED:
            self._stage_paid_sale(sale, attempt, owner_id=owner_id)
        self._commit()
        logger.info(
            "Payment attempt created sale=%s attempt=%s method=%s",
            sale.id,
            attempt.id,
            payment_method.value,
        )
        return CheckoutStart(
            sale.id,
            attempt.id,
            request,
            payment_method is PaymentMethod.CARD,
        )

    def process_payment(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        attempt_id: UUIDv7 | None = None,
    ) -> Sale:
        """Perform one worker-owned acquiring initiate or reconciliation step."""
        sale = self._require_owned(sale_id, owner_id)
        attempt = (
            self._payment_by_id(sale, attempt_id)
            if attempt_id is not None
            else self._latest_payment(sale)
        )
        if attempt.status is PaymentStatus.SUCCEEDED:
            return sale
        fallback = self._emergency_fallback_payment(sale)
        if fallback is not None and attempt.id != fallback.id and attempt.external_id is None:
            # A command without a provider ID may be in-flight, but starting it
            # again after an external-POS fallback could charge the customer twice.
            return sale
        request = self._payment_request(sale, attempt)
        start = CheckoutStart(sale.id, attempt.id, request, False)
        try:
            if attempt.external_id:
                operation = self._require_payment_provider().get_payment(
                    attempt.external_id,
                    request,
                )
            elif attempt.status is PaymentStatus.PENDING:
                operation = self._require_payment_provider().initiate_payment(request)
            else:
                return sale
        except ProviderCallError as exc:
            if attempt.external_id:
                return self._record_payment_poll_error(sale.id, attempt.id, owner_id, exc)
            return self._record_payment_error(start, owner_id=owner_id, error=exc)
        return self._record_payment_operation(start, owner_id=owner_id, operation=operation)

    def _record_payment_operation(
        self,
        start: CheckoutStart,
        *,
        owner_id: UUIDv7,
        operation: ProviderOperation,
    ) -> Sale:
        sale = self._locked_owned(start.sale_id, owner_id)
        attempt = self._payment_by_id(sale, start.attempt_id)
        if attempt.status is PaymentStatus.SUCCEEDED:
            self._commit()
            return self._advance_paid_sale(
                sale.id, owner_id=owner_id, evidence=operation.fiscal_evidence
            )
        previous_status = attempt.status
        previous_external_id = attempt.external_id
        fallback = self._emergency_fallback_payment(sale)
        externally_accounted = fallback is not None and fallback.id != attempt.id
        self._apply_common_operation(attempt, operation)
        if operation.state is ProviderOutcomeState.SUCCEEDED:
            if not (operation.external_id or attempt.external_id):
                attempt.status = PaymentStatus.UNKNOWN
                sale.status = SaleStatus.PAYMENT_PENDING
                attempt.error_code = "missing_external_id"
                attempt.error_message = "Provider confirmed payment without an operation ID."
            else:
                attempt.status = PaymentStatus.SUCCEEDED
                if externally_accounted:
                    attempt.provider_metadata = {
                        **attempt.provider_metadata,
                        "accounting_superseded": True,
                        "late_provider_approval_detected": True,
                        "effective_payment_attempt_id": str(fallback.id),
                    }
                else:
                    sale.status = SaleStatus.PAID
                    sale.paid_at = datetime.now(UTC)
        elif operation.state is ProviderOutcomeState.FAILED:
            attempt.status = PaymentStatus.FAILED
            if not externally_accounted:
                sale.status = SaleStatus.DRAFT
        elif operation.state is ProviderOutcomeState.CANCELED:
            attempt.status = PaymentStatus.CANCELED
            if not externally_accounted:
                sale.status = SaleStatus.DRAFT
        elif operation.state is ProviderOutcomeState.UNKNOWN:
            attempt.status = PaymentStatus.UNKNOWN
            if not externally_accounted:
                sale.status = SaleStatus.PAYMENT_PENDING
        else:
            cancellation = attempt.provider_metadata.get(
                "device_operation_cancellation"
            ) or attempt.provider_metadata.get("emergency_cancellation")
            attempt.status = (
                PaymentStatus.UNKNOWN
                if isinstance(cancellation, dict)
                and cancellation.get("state") == "unknown"
                else PaymentStatus.PENDING
            )
            if not externally_accounted:
                sale.status = SaleStatus.PAYMENT_PENDING
        attempt.updated_by_id = owner_id
        sale.updated_by_id = owner_id
        correlation_id = self._payment_correlation(attempt)
        if previous_external_id is None and attempt.external_id is not None:
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
                entity_id=attempt.id,
                event_type=FinancialEventType.PAYMENT_OPERATION_ID_RECEIVED,
                correlation_id=correlation_id,
                actor_id=owner_id,
                previous_status=previous_status.value,
                new_status=attempt.status.value,
                provider=attempt.provider,
                provider_operation_id=attempt.external_id,
            )
        payment_event = {
            PaymentStatus.SUCCEEDED: FinancialEventType.PAYMENT_APPROVED,
            PaymentStatus.FAILED: FinancialEventType.PAYMENT_DECLINED,
            PaymentStatus.CANCELED: FinancialEventType.PAYMENT_CANCELED,
            PaymentStatus.UNKNOWN: FinancialEventType.PAYMENT_OUTCOME_UNKNOWN,
        }.get(attempt.status)
        if payment_event is not None and attempt.status is not previous_status:
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
                entity_id=attempt.id,
                event_type=payment_event,
                correlation_id=correlation_id,
                actor_id=owner_id,
                previous_status=previous_status.value,
                new_status=attempt.status.value,
                provider=attempt.provider,
                provider_operation_id=attempt.external_id,
                error_code=attempt.error_code,
                safe_message=attempt.error_message,
            )
        if attempt.status is PaymentStatus.SUCCEEDED:
            if externally_accounted:
                self._event(
                    sale=sale,
                    entity_type=FinancialEntityType.RECONCILIATION,
                    entity_id=attempt.id,
                    event_type=FinancialEventType.LATE_PROVIDER_APPROVAL_DETECTED,
                    correlation_id=correlation_id,
                    actor_id=owner_id,
                    previous_status=previous_status.value,
                    new_status=PaymentStatus.SUCCEEDED.value,
                    provider=attempt.provider,
                    provider_operation_id=attempt.external_id,
                    safe_message=(
                        "Провайдер подтвердил исходную оплату после ручной оплаты на кассе; "
                        "проверьте возможное двойное списание и необходимость возврата."
                    ),
                    metadata={"effective_payment_attempt_id": str(fallback.id)},
                )
                self._event(
                    sale=sale,
                    entity_type=FinancialEntityType.RECONCILIATION,
                    entity_id=attempt.id,
                    event_type=FinancialEventType.MANUAL_REVIEW_REQUIRED,
                    correlation_id=correlation_id,
                    actor_id=owner_id,
                    provider=attempt.provider,
                    provider_operation_id=attempt.external_id,
                    safe_message=(
                        "Возможное двойное списание: требуется проверка и отдельное "
                        "решение о возврате."
                    ),
                )
            else:
                self._stage_paid_sale(sale, attempt, owner_id=owner_id)
        self._commit()
        logger.info(
            "Payment state sale=%s attempt=%s external=%s state=%s",
            sale.id,
            attempt.id,
            attempt.external_id,
            attempt.status.value,
        )
        if attempt.status is PaymentStatus.SUCCEEDED:
            logger.info(
                "Checkout timing sale=%s stage=T0 payment_operation=%s "
                "payment_approved_at=%s provider_completed_at=%s",
                sale.id,
                attempt.external_id,
                sale.paid_at.isoformat() if sale.paid_at else None,
                attempt.provider_metadata.get("provider_updated_at"),
            )
        return sale

    def _record_payment_error(
        self, start: CheckoutStart, *, owner_id: UUIDv7, error: ProviderCallError
    ) -> Sale:
        sale = self._locked_owned(start.sale_id, owner_id)
        attempt = self._payment_by_id(sale, start.attempt_id)
        previous_status = attempt.status
        attempt.status = PaymentStatus.UNKNOWN if error.outcome_unknown else PaymentStatus.FAILED
        attempt.error_code = error.code
        attempt.error_message = str(error)
        attempt.provider_metadata = {
            **attempt.provider_metadata,
            "error_kind": error.kind.value,
        }
        attempt.updated_by_id = owner_id
        sale.status = SaleStatus.PAYMENT_PENDING if error.outcome_unknown else SaleStatus.DRAFT
        if self._emergency_fallback_payment(sale) is not None:
            sale.status = SaleStatus.COMPLETED
        sale.updated_by_id = owner_id
        if attempt.status is not previous_status:
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
                entity_id=attempt.id,
                event_type=(
                    FinancialEventType.PAYMENT_OUTCOME_UNKNOWN
                    if error.outcome_unknown
                    else FinancialEventType.PAYMENT_DECLINED
                ),
                correlation_id=self._payment_correlation(attempt),
                actor_id=owner_id,
                previous_status=previous_status.value,
                new_status=attempt.status.value,
                provider=attempt.provider,
                provider_operation_id=attempt.external_id,
                error_code=error.code,
                safe_message=str(error),
            )
        self._commit()
        logger.warning(
            "Payment call unresolved sale=%s attempt=%s state=%s code=%s",
            sale.id,
            attempt.id,
            attempt.status.value,
            error.code,
        )
        return sale

    def _record_payment_poll_error(
        self,
        sale_id: UUIDv7,
        attempt_id: UUIDv7,
        owner_id: UUIDv7,
        error: ProviderCallError,
    ) -> Sale:
        sale = self._locked_owned(sale_id, owner_id)
        attempt = self._payment_by_id(sale, attempt_id)
        previous_status = attempt.status
        attempt.status = PaymentStatus.UNKNOWN
        attempt.error_code = error.code
        attempt.error_message = str(error)
        attempt.provider_metadata = {
            **attempt.provider_metadata,
            "error_kind": error.kind.value,
        }
        sale.status = SaleStatus.PAYMENT_PENDING
        if self._emergency_fallback_payment(sale) is not None:
            sale.status = SaleStatus.COMPLETED
        if previous_status is not PaymentStatus.UNKNOWN:
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
                entity_id=attempt.id,
                event_type=FinancialEventType.PAYMENT_OUTCOME_UNKNOWN,
                correlation_id=self._payment_correlation(attempt),
                actor_id=owner_id,
                previous_status=previous_status.value,
                new_status=PaymentStatus.UNKNOWN.value,
                provider=attempt.provider,
                provider_operation_id=attempt.external_id,
                error_code=error.code,
                safe_message=str(error),
            )
        self._commit()
        return sale

    def _mark_payment_unknown_without_external_id(
        self, sale_id: UUIDv7, attempt_id: UUIDv7, owner_id: UUIDv7
    ) -> Sale:
        sale = self._locked_owned(sale_id, owner_id)
        attempt = self._payment_by_id(sale, attempt_id)
        previous_status = attempt.status
        attempt.status = PaymentStatus.UNKNOWN
        attempt.error_code = "missing_external_id"
        attempt.error_message = "Payment outcome is unknown; no provider operation ID is available."
        if self._emergency_fallback_payment(sale) is None:
            sale.status = SaleStatus.PAYMENT_PENDING
        self._event(
            sale=sale,
            entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
            entity_id=attempt.id,
            event_type=FinancialEventType.PAYMENT_OUTCOME_UNKNOWN,
            correlation_id=self._payment_correlation(attempt),
            actor_id=owner_id,
            previous_status=previous_status.value,
            new_status=PaymentStatus.UNKNOWN.value,
            provider=attempt.provider,
            error_code=attempt.error_code,
            safe_message=attempt.error_message,
        )
        self._commit()
        return sale

    def _advance_paid_sale(
        self, sale_id: UUIDv7, *, owner_id: UUIDv7, evidence: object | None = None
    ) -> Sale:
        del evidence
        sale = self._locked_owned(sale_id, owner_id)
        if sale.status not in {SaleStatus.PAID, SaleStatus.FISCALIZATION_PENDING}:
            self._commit()
            return sale
        payment = self._successful_payment(sale)
        self._stage_paid_sale(sale, payment, owner_id=owner_id)
        self._commit()
        return sale

    def _stage_paid_sale(self, sale: Sale, payment: PaymentAttempt, *, owner_id: UUIDv7) -> None:
        """Atomically stage Inventory and durable fiscal work with payment approval."""
        if sale.inventory_posted_at is None:
            self._inventory.create_sale_movements_once(
                sale.id,
                [
                    (item.variant_id, item.quantity)
                    for item in sale.items
                    if item.variant_id is not None
                ],
                actor_id=owner_id,
            )
            sale.inventory_posted_at = datetime.now(UTC)
        if not payment.fiscalization_required:
            if sale.fiscalization is None:
                fiscalization = Fiscalization(
                    sale_id=sale.id,
                    payment_attempt_id=payment.id,
                    integration_id=None,
                    provider=None,
                    status=FiscalizationStatus.SKIPPED,
                    idempotency_key=f"sale:{sale.id}:fiscalization:skipped",
                    attempt_count=0,
                    fiscal_amount=sale.total_amount,
                    currency=sale.currency,
                    provider_metadata=(
                        {
                            "reason": "external_pos_receipt_confirmed",
                            "source": "EXTERNAL_POS",
                            "confirmation": "OPERATOR_CONFIRMED",
                            "external_receipt_confirmed": True,
                        }
                        if payment.emergency_fallback
                        else {"reason": "operator_selected_cash_without_receipt"}
                    ),
                    created_by_id=owner_id,
                    updated_by_id=owner_id,
                )
                sale.fiscalization = fiscalization
                self._sales.add_fiscalization(fiscalization)
                self._session.flush()
                self._event(
                    sale=sale,
                    entity_type=FinancialEntityType.FISCALIZATION,
                    entity_id=fiscalization.id,
                    event_type=FinancialEventType.FISCALIZATION_CREATED,
                    correlation_id=f"fiscal:{fiscalization.id}",
                    actor_id=owner_id,
                    new_status=FiscalizationStatus.SKIPPED.value,
                    provider=None,
                    safe_message=(
                        "Оператор сообщил о чеке на физической кассе; CORE не формирует "
                        "второй чек до отдельной проверки."
                        if payment.emergency_fallback
                        else "Оператор выбрал продажу без фискального чека."
                    ),
                )
            sale.status = SaleStatus.COMPLETED
            sale.completed_at = datetime.now(UTC)
            sale.updated_by_id = owner_id
            logger.info("Fiscalization skipped by checkout option sale=%s", sale.id)
            return
        binding = self._require_binding()
        self._require_fiscal_provider()
        if sale.fiscalization is None:
            fiscalization = Fiscalization(
                sale_id=sale.id,
                payment_attempt_id=payment.id,
                integration_id=binding.resolved_fiscal_integration_id,
                provider=binding.resolved_fiscal_provider,
                status=FiscalizationStatus.PENDING,
                idempotency_key=f"sale:{sale.id}:fiscalization:1",
                attempt_count=1,
                fiscal_amount=sale.total_amount,
                currency=sale.currency,
                provider_metadata={},
                created_by_id=owner_id,
                updated_by_id=owner_id,
            )
            sale.fiscalization = fiscalization
            self._sales.add_fiscalization(fiscalization)
            self._session.flush()
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.FISCALIZATION,
                entity_id=fiscalization.id,
                event_type=FinancialEventType.FISCALIZATION_CREATED,
                correlation_id=f"fiscal:{fiscalization.id}",
                actor_id=owner_id,
                new_status=FiscalizationStatus.PENDING.value,
                provider=fiscalization.provider,
            )
        fiscalization = sale.fiscalization
        if fiscalization is None:
            raise CheckoutStateError("Fiscalization record is missing.")
        if sale.fiscalization_job is None:
            job = FiscalizationJob(
                sale_id=sale.id,
                fiscalization_id=fiscalization.id,
                status=FiscalizationJobStatus.QUEUED,
                attempt_count=0,
                next_retry_at=datetime.now(UTC),
                correlation_id=f"fiscal:{fiscalization.id}:attempt:{fiscalization.attempt_count}",
                created_by_id=owner_id,
                updated_by_id=owner_id,
            )
            sale.fiscalization_job = job
            self._sales.add_fiscalization_job(job)
            self._session.flush()
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.FISCALIZATION_JOB,
                entity_id=job.id,
                event_type=FinancialEventType.FISCAL_COMMAND_PREPARED,
                correlation_id=job.correlation_id,
                actor_id=owner_id,
                new_status=FiscalizationJobStatus.QUEUED.value,
                provider=fiscalization.provider,
            )
        sale.status = SaleStatus.FISCALIZATION_PENDING
        sale.updated_by_id = owner_id

    def process_fiscalization(self, sale_id: UUIDv7, *, owner_id: UUIDv7) -> Sale:
        """Perform one worker-owned initiate or status-poll step."""
        sale = self._require_owned(sale_id, owner_id)
        fiscalization = sale.fiscalization
        if fiscalization is None:
            raise CheckoutStateError("Fiscalization record is missing.")
        if fiscalization.operator_dismissed:
            return sale
        if fiscalization.status is FiscalizationStatus.SUCCEEDED:
            return sale
        if fiscalization.external_id:
            return self._progress_fiscalization(sale_id, owner_id=owner_id)
        if fiscalization.status is FiscalizationStatus.PENDING:
            return self._initiate_fiscalization(sale_id, owner_id=owner_id)
        # UNKNOWN without an operation ID is a quarantined cross-system gap.
        # A lookup miss (and especially the unavailable cash lookup) cannot prove
        # that the original receipt command failed, so never issue another POST.
        return sale

    def _initiate_fiscalization(
        self, sale_id: UUIDv7, *, owner_id: UUIDv7, evidence: object | None = None
    ) -> Sale:
        sale = self._require_owned(sale_id, owner_id)
        fiscalization = sale.fiscalization
        payment = self._successful_payment(sale)
        fiscal_provider = self._require_fiscal_provider()
        if fiscalization is None:
            raise CheckoutStateError("Fiscalization record is missing.")
        if evidence is None and payment.payment_method is PaymentMethod.CARD:
            if payment.external_id is None:
                return self._mark_fiscal_unknown(
                    sale.id, owner_id, "missing_payment_id", "Payment evidence is unavailable."
                )
            try:
                payment_state = self._require_payment_provider().get_payment(
                    payment.external_id, self._payment_request(sale, payment)
                )
            except ProviderCallError as exc:
                return self._mark_fiscal_unknown(sale.id, owner_id, exc.code, str(exc))
            evidence = payment_state.fiscal_evidence
        if evidence is None and payment.payment_method is PaymentMethod.CARD:
            return self._mark_fiscal_unknown(
                sale.id,
                owner_id,
                "missing_payment_evidence",
                "Fiscal payment evidence is unavailable.",
            )
        request = self._fiscal_request(sale, fiscalization, payment, evidence)
        lookup_started = perf_counter()
        try:
            existing = fiscal_provider.find_fiscalization(request)
        except ProviderCallError as exc:
            return self._record_fiscal_error(sale.id, owner_id, exc)
        logger.info(
            "Checkout timing sale=%s stage=receipt_preflight lookup_ms=%.1f found=%s",
            sale.id,
            (perf_counter() - lookup_started) * 1000,
            existing is not None,
        )
        if existing is not None:
            return self._record_fiscal_operation(sale.id, owner_id, existing)
        try:
            operation = fiscal_provider.initiate_fiscalization(request)
        except ProviderCallError as exc:
            return self._record_fiscal_error(sale.id, owner_id, exc)
        return self._record_fiscal_operation(sale.id, owner_id, operation)

    def _progress_fiscalization(self, sale_id: UUIDv7, *, owner_id: UUIDv7) -> Sale:
        sale = self._require_owned(sale_id, owner_id)
        fiscalization = sale.fiscalization
        if fiscalization is None:
            raise CheckoutStateError("Fiscalization record is missing.")
        if fiscalization.external_id is None:
            if fiscalization.status is FiscalizationStatus.PENDING:
                return self._mark_fiscal_unknown(
                    sale.id,
                    owner_id,
                    "missing_external_id",
                    "Fiscalization outcome is unknown; no provider operation ID is available.",
                )
            return sale
        payment = self._successful_payment(sale)
        fiscal_provider = self._require_fiscal_provider()
        try:
            request = self._fiscal_request(sale, fiscalization, payment, None)
            operation = fiscal_provider.get_fiscalization(fiscalization.external_id, request)
        except ProviderCallError as exc:
            return self._mark_fiscal_unknown(sale.id, owner_id, exc.code, str(exc))
        return self._record_fiscal_operation(sale.id, owner_id, operation)

    def _record_fiscal_operation(
        self, sale_id: UUIDv7, owner_id: UUIDv7, operation: ProviderOperation
    ) -> Sale:
        sale = self._locked_owned(sale_id, owner_id)
        fiscalization = sale.fiscalization
        if fiscalization is None:
            self._rollback()
            raise CheckoutStateError("Fiscalization record is missing.")
        previous_status = fiscalization.status
        previous_external_id = fiscalization.external_id
        self._apply_common_operation(fiscalization, operation)
        if operation.state is ProviderOutcomeState.SUCCEEDED:
            fiscalization.status = FiscalizationStatus.SUCCEEDED
            fiscalization.external_receipt_id = operation.external_reference
            sale.status = SaleStatus.COMPLETED
            sale.completed_at = datetime.now(UTC)
            fiscalization.provider_metadata = {
                **fiscalization.provider_metadata,
                "core_persisted_at": sale.completed_at.isoformat(),
            }
        elif operation.state is ProviderOutcomeState.FAILED:
            fiscalization.status = FiscalizationStatus.FAILED
            sale.status = SaleStatus.FISCALIZATION_FAILED
        elif operation.state is ProviderOutcomeState.UNKNOWN:
            fiscalization.status = FiscalizationStatus.UNKNOWN
            sale.status = SaleStatus.FISCALIZATION_PENDING
        else:
            fiscalization.status = FiscalizationStatus.PENDING
            sale.status = SaleStatus.FISCALIZATION_PENDING
        fiscalization.updated_by_id = owner_id
        sale.updated_by_id = owner_id
        correlation_id = self._fiscal_correlation(sale)
        if previous_external_id is None and fiscalization.external_id is not None:
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.FISCALIZATION,
                entity_id=fiscalization.id,
                event_type=FinancialEventType.FISCAL_OPERATION_ID_RECEIVED,
                correlation_id=correlation_id,
                actor_id=owner_id,
                previous_status=previous_status.value,
                new_status=fiscalization.status.value,
                provider=fiscalization.provider,
                provider_operation_id=fiscalization.external_id,
            )
        fiscal_event = {
            FiscalizationStatus.SUCCEEDED: FinancialEventType.FISCALIZATION_SUCCEEDED,
            FiscalizationStatus.FAILED: FinancialEventType.FISCALIZATION_FAILED,
            FiscalizationStatus.UNKNOWN: FinancialEventType.FISCAL_OUTCOME_UNKNOWN,
        }.get(fiscalization.status)
        if fiscal_event is not None and fiscalization.status is not previous_status:
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.FISCALIZATION,
                entity_id=fiscalization.id,
                event_type=fiscal_event,
                correlation_id=correlation_id,
                actor_id=owner_id,
                previous_status=previous_status.value,
                new_status=fiscalization.status.value,
                provider=fiscalization.provider,
                provider_operation_id=fiscalization.external_id,
                error_code=fiscalization.error_code,
                safe_message=fiscalization.error_message,
            )
        self._commit()
        if operation.state is ProviderOutcomeState.SUCCEEDED:
            logger.info(
                "Checkout timing sale=%s stage=T4 payment_approved_at=%s "
                "receipt_started_at=%s receipt_response_at=%s provider_completed_at=%s "
                "core_persisted_at=%s",
                sale.id,
                sale.paid_at.isoformat() if sale.paid_at else None,
                fiscalization.provider_metadata.get("receipt_process_started_at"),
                fiscalization.provider_metadata.get("receipt_process_response_at"),
                fiscalization.provider_metadata.get("provider_updated_at"),
                fiscalization.provider_metadata.get("core_persisted_at"),
            )
        logger.info(
            "Fiscal state sale=%s fiscalization=%s external=%s state=%s",
            sale.id,
            fiscalization.id,
            fiscalization.external_id,
            fiscalization.status.value,
        )
        return sale

    def _record_fiscal_error(
        self, sale_id: UUIDv7, owner_id: UUIDv7, error: ProviderCallError
    ) -> Sale:
        sale = self._locked_owned(sale_id, owner_id)
        fiscalization = sale.fiscalization
        if fiscalization is None:
            self._rollback()
            raise CheckoutStateError("Fiscalization record is missing.")
        previous_status = fiscalization.status
        temporary_query_failure = (
            error.kind is ProviderErrorKind.TEMPORARY_STATUS_QUERY_FAILURE
        )
        fiscalization.status = (
            FiscalizationStatus.PENDING
            if temporary_query_failure
            else FiscalizationStatus.UNKNOWN
            if error.outcome_unknown
            else FiscalizationStatus.FAILED
        )
        fiscalization.error_code = error.code
        fiscalization.error_message = str(error)
        fiscalization.provider_metadata = {
            **fiscalization.provider_metadata,
            "error_kind": error.kind.value,
        }
        sale.status = (
            SaleStatus.FISCALIZATION_PENDING
            if error.outcome_unknown or temporary_query_failure
            else SaleStatus.FISCALIZATION_FAILED
        )
        if fiscalization.status is not previous_status and not temporary_query_failure:
            self._event(
                sale=sale,
                entity_type=FinancialEntityType.FISCALIZATION,
                entity_id=fiscalization.id,
                event_type=(
                    FinancialEventType.FISCAL_OUTCOME_UNKNOWN
                    if error.outcome_unknown
                    else FinancialEventType.FISCALIZATION_FAILED
                ),
                correlation_id=self._fiscal_correlation(sale),
                actor_id=owner_id,
                previous_status=previous_status.value,
                new_status=fiscalization.status.value,
                provider=fiscalization.provider,
                provider_operation_id=fiscalization.external_id,
                error_code=error.code,
                safe_message=str(error),
            )
        self._commit()
        return sale

    def _mark_fiscal_unknown(
        self, sale_id: UUIDv7, owner_id: UUIDv7, code: str, message: str
    ) -> Sale:
        return self._record_fiscal_error(
            sale_id,
            owner_id,
            ProviderCallError(code, message, outcome_unknown=True),
        )

    def _payment_request(self, sale: Sale, attempt: PaymentAttempt) -> PaymentRequest:
        return PaymentRequest(
            sale_id=sale.id,
            attempt_id=attempt.id,
            idempotency_key=attempt.idempotency_key,
            amount=attempt.requested_amount,
            currency=attempt.currency,
            method=attempt.payment_method,
        )

    @staticmethod
    def _fiscal_request(
        sale: Sale,
        fiscalization: Fiscalization,
        payment: PaymentAttempt,
        evidence: object | None,
    ) -> FiscalRequest:
        return FiscalRequest(
            sale_id=sale.id,
            fiscalization_id=fiscalization.id,
            idempotency_key=fiscalization.idempotency_key,
            amount=fiscalization.fiscal_amount,
            currency=fiscalization.currency,
            payment_method=payment.payment_method,
            lines=tuple(
                FiscalLine(
                    item_id=item.id,
                    allocation_index=index,
                    variant_id=item.variant_id,
                    name=item.display_label_snapshot,
                    sku=item.sku_snapshot,
                    barcode=item.barcode_snapshot,
                    quantity=int(allocation["quantity"]),
                    unit_price=Decimal(str(allocation["unit_price"])),
                    line_total=Decimal(str(allocation["unit_price"])) * int(allocation["quantity"]),
                )
                for item in sale.items
                for index, allocation in enumerate(item.fiscal_allocations)
            ),
            payment_evidence=evidence,
        )

    @staticmethod
    def _apply_common_operation(
        record: PaymentAttempt | Fiscalization, operation: ProviderOperation
    ) -> None:
        if operation.external_id:
            record.external_id = operation.external_id
        record.provider_metadata = {**record.provider_metadata, **operation.metadata}
        record.error_code = operation.error_code
        record.error_message = operation.error_message

    def _ensure_binding_matches(self, sale: Sale, *, allow_without_attempt: bool = False) -> None:
        if not sale.payments and allow_without_attempt:
            return
        binding = self._require_binding()
        payment = self._latest_payment(sale)
        if (
            payment.integration_id is not None
            and payment.integration_id != binding.integration_id
        ):
            raise CheckoutProviderMismatchError
        fiscal_integration_id = (
            sale.fiscalization.integration_id
            if sale.fiscalization is not None
            else self._fiscal_integration_id(payment)
        )
        if (
            fiscal_integration_id is not None
            and fiscal_integration_id != binding.resolved_fiscal_integration_id
        ):
            raise CheckoutProviderMismatchError

    def _require_binding(self) -> CheckoutProviderBinding:
        if self._binding is None:
            raise CheckoutStateError("A configured fiscal provider is required.")
        return self._binding

    def _require_payment_provider(self) -> PaymentProvider:
        """Return the selected payment capability without assuming a shared provider."""
        provider = self._require_binding().payment
        if provider is None:
            raise CheckoutStateError("A configured payment provider is required.")
        return provider

    def _require_fiscal_provider(self) -> FiscalProvider:
        """Return the selected fiscal capability without assuming a shared provider."""
        provider = self._require_binding().fiscal
        if provider is None:
            raise CheckoutStateError("A configured fiscal provider is required.")
        return provider

    @staticmethod
    def _initial_payment_metadata(
        *,
        payment_method: PaymentMethod,
        fiscalization_required: bool,
        binding: CheckoutProviderBinding | None,
    ) -> dict[str, object]:
        """Persist the selected fiscal capability before asynchronous payment work."""
        metadata: dict[str, object] = {}
        if payment_method is PaymentMethod.CASH:
            metadata["confirmed_by_operator"] = True
        if fiscalization_required and binding is not None:
            metadata.update(
                {
                    "fiscal_integration_id": str(binding.resolved_fiscal_integration_id),
                    "fiscal_provider": binding.resolved_fiscal_provider.value,
                }
            )
        return metadata

    @staticmethod
    def _fiscal_integration_id(payment: PaymentAttempt) -> UUIDv7 | None:
        """Read a persisted fiscal selection with legacy same-provider fallback."""
        try:
            return payment.selected_fiscal_integration_id
        except ValueError as exc:
            raise CheckoutStateError("Persisted fiscal provider selection is invalid.") from exc

    @staticmethod
    def _payment_facts(
        option: CheckoutPaymentOption,
    ) -> tuple[PaymentMethod, bool]:
        if option is CheckoutPaymentOption.CARD:
            return PaymentMethod.CARD, True
        if option is CheckoutPaymentOption.CASH_WITH_RECEIPT:
            return PaymentMethod.CASH, True
        if option is CheckoutPaymentOption.CASH_WITHOUT_RECEIPT:
            return PaymentMethod.CASH, False
        raise CheckoutStateError("Unsupported checkout payment option.")

    @staticmethod
    def _latest_payment(sale: Sale) -> PaymentAttempt:
        if not sale.payments:
            raise CheckoutStateError("Payment attempt is missing.")
        return max(sale.payments, key=lambda item: item.attempt_number)

    @staticmethod
    def _successful_payment(sale: Sale) -> PaymentAttempt:
        attempt = next(
            (
                item
                for item in reversed(sale.payments)
                if item.status is PaymentStatus.SUCCEEDED and not item.accounting_superseded
            ),
            None,
        )
        if attempt is None:
            raise CheckoutStateError("Successful payment is missing.")
        return attempt

    @staticmethod
    def _emergency_fallback_payment(sale: Sale) -> PaymentAttempt | None:
        """Return the single effective external-POS confirmation, if recorded."""
        return next((item for item in reversed(sale.payments) if item.emergency_fallback), None)

    @staticmethod
    def _payment_by_id(sale: Sale, attempt_id: UUIDv7) -> PaymentAttempt:
        attempt = next((item for item in sale.payments if item.id == attempt_id), None)
        if attempt is None:
            raise CheckoutStateError("Payment attempt is missing.")
        return attempt

    def _require_owned(self, sale_id: UUIDv7, owner_id: UUIDv7) -> Sale:
        sale = self._sales.get_owned(sale_id, owner_id)
        if sale is None:
            raise SaleNotFoundError
        return sale

    def _locked_owned(self, sale_id: UUIDv7, owner_id: UUIDv7) -> Sale:
        sale = self._sales.get_owned_for_update(sale_id, owner_id)
        if sale is None:
            raise SaleNotFoundError
        return sale

    def _commit(self) -> None:
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def _rollback(self) -> None:
        self._session.rollback()

    def _event(
        self,
        *,
        sale: Sale,
        entity_type: FinancialEntityType,
        entity_id: UUIDv7,
        event_type: FinancialEventType,
        correlation_id: str,
        actor_id: UUIDv7,
        previous_status: str | None = None,
        new_status: str | None = None,
        provider: IntegrationProvider | None = None,
        provider_operation_id: str | None = None,
        error_code: str | None = None,
        safe_message: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        append_financial_event(
            self._session,
            sale_id=sale.id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            correlation_id=correlation_id,
            actor_type=self._actor_type,
            actor_id=actor_id if self._actor_type is FinancialActorType.OPERATOR else None,
            previous_status=previous_status,
            new_status=new_status,
            provider=provider,
            provider_operation_id=provider_operation_id,
            error_code=error_code,
            safe_message=safe_message,
            metadata=metadata,
        )

    @staticmethod
    def _payment_correlation(attempt: PaymentAttempt) -> str:
        return attempt.job.correlation_id if attempt.job is not None else f"payment:{attempt.id}"

    @staticmethod
    def _fiscal_correlation(sale: Sale) -> str:
        if sale.fiscalization_job is not None:
            return sale.fiscalization_job.correlation_id
        if sale.fiscalization is None:
            return f"sale:{sale.id}:fiscal"
        return f"fiscal:{sale.fiscalization.id}"
