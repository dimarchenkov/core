from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from core.inventory.service import InventoryService
from core.sales.enums import (
    FiscalizationStatus,
    PaymentMethod,
    PaymentStatus,
    SaleStatus,
)
from core.sales.models import Fiscalization, PaymentAttempt, Sale
from core.sales.providers import (
    CheckoutProviderBinding,
    FiscalLine,
    FiscalRequest,
    PaymentRequest,
    ProviderCallError,
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

    def __init__(self, session: Session, binding: CheckoutProviderBinding) -> None:
        """Create a checkout service with generic provider capabilities."""
        self._session = session
        self._binding = binding
        self._sales = SaleRepository(session)
        self._inventory = InventoryService(session)

    def start(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        payment_method: PaymentMethod = PaymentMethod.CARD,
    ) -> Sale:
        """Freeze one DRAFT and persist one card or cash payment fact."""
        start = self._prepare_payment(
            sale_id,
            owner_id=owner_id,
            retry=False,
            payment_method=payment_method,
        )
        if start.request.method is PaymentMethod.CASH:
            return self._advance_paid_sale(start.sale_id, owner_id=owner_id)
        if not start.initiate:
            return self._require_owned(sale_id, owner_id)
        try:
            operation = self._binding.payment.initiate_payment(start.request)
        except ProviderCallError as exc:
            return self._record_payment_error(start, owner_id=owner_id, error=exc)
        return self._record_payment_operation(start, owner_id=owner_id, operation=operation)

    def retry_payment(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        payment_method: PaymentMethod = PaymentMethod.CARD,
    ) -> Sale:
        """Create a new attempt only after the prior result is failed or canceled."""
        start = self._prepare_payment(
            sale_id,
            owner_id=owner_id,
            retry=True,
            payment_method=payment_method,
        )
        if start.request.method is PaymentMethod.CASH:
            return self._advance_paid_sale(start.sale_id, owner_id=owner_id)
        try:
            operation = self._binding.payment.initiate_payment(start.request)
        except ProviderCallError as exc:
            return self._record_payment_error(start, owner_id=owner_id, error=exc)
        return self._record_payment_operation(start, owner_id=owner_id, operation=operation)

    def progress(self, sale_id: UUIDv7, *, owner_id: UUIDv7) -> Sale:
        """Recover provider state and advance only safe, persisted lifecycle transitions."""
        sale = self._require_owned(sale_id, owner_id)
        self._ensure_binding_matches(sale)
        if sale.status is SaleStatus.PAYMENT_PENDING:
            attempt = self._latest_payment(sale)
            if attempt.external_id is None:
                if attempt.status is PaymentStatus.PENDING:
                    return self._mark_payment_unknown_without_external_id(
                        sale.id, attempt.id, owner_id
                    )
                return sale
            request = self._payment_request(sale, attempt)
            try:
                operation = self._binding.payment.get_payment(attempt.external_id, request)
            except ProviderCallError as exc:
                return self._record_payment_poll_error(sale.id, attempt.id, owner_id, exc)
            sale = self._record_payment_operation(
                CheckoutStart(sale.id, attempt.id, request, False),
                owner_id=owner_id,
                operation=operation,
            )
        if sale.status is SaleStatus.PAID:
            return self._advance_paid_sale(sale.id, owner_id=owner_id)
        if sale.status is SaleStatus.FISCALIZATION_PENDING:
            return self._progress_fiscalization(sale.id, owner_id=owner_id)
        return sale

    def retry_fiscalization(self, sale_id: UUIDv7, *, owner_id: UUIDv7) -> Sale:
        """Retry a definitely failed receipt without ever initiating payment again."""
        sale = self._locked_owned(sale_id, owner_id)
        self._ensure_binding_matches(sale)
        fiscalization = sale.fiscalization
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
        sale.status = SaleStatus.FISCALIZATION_PENDING
        sale.updated_by_id = owner_id
        self._commit()
        return self._initiate_fiscalization(sale.id, owner_id=owner_id)

    def _prepare_payment(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        retry: bool,
        payment_method: PaymentMethod,
    ) -> CheckoutStart:
        sale = self._locked_owned(sale_id, owner_id)
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
            ):
                self._ensure_binding_matches(sale)
                request = self._payment_request(sale, successful)
                self._commit()
                return CheckoutStart(sale.id, successful.id, request, False)
            self._rollback()
            raise CheckoutStateError(f"Sale cannot start payment from {sale.status.value}.")
        if sale.status is SaleStatus.PAYMENT_PENDING and not retry:
            if payment_method is not PaymentMethod.CARD:
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
        attempt_number = len(sale.payments) + 1
        attempt = PaymentAttempt(
            sale_id=sale.id,
            integration_id=(
                self._binding.integration_id if payment_method is PaymentMethod.CARD else None
            ),
            provider=self._binding.provider if payment_method is PaymentMethod.CARD else None,
            payment_method=payment_method,
            requested_amount=sale.total_amount,
            currency=sale.currency,
            status=(
                PaymentStatus.SUCCEEDED
                if payment_method is PaymentMethod.CASH
                else PaymentStatus.PENDING
            ),
            idempotency_key=f"sale:{sale.id}:payment:{attempt_number}",
            attempt_number=attempt_number,
            provider_metadata=(
                {"confirmed_by_operator": True}
                if payment_method is PaymentMethod.CASH
                else {}
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
        request = self._payment_request(sale, attempt)
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
        self._apply_common_operation(attempt, operation)
        if operation.state is ProviderOutcomeState.SUCCEEDED:
            if not (operation.external_id or attempt.external_id):
                attempt.status = PaymentStatus.UNKNOWN
                sale.status = SaleStatus.PAYMENT_PENDING
                attempt.error_code = "missing_external_id"
                attempt.error_message = "Provider confirmed payment without an operation ID."
            else:
                attempt.status = PaymentStatus.SUCCEEDED
                sale.status = SaleStatus.PAID
                sale.paid_at = datetime.now(UTC)
        elif operation.state is ProviderOutcomeState.FAILED:
            attempt.status = PaymentStatus.FAILED
            sale.status = SaleStatus.DRAFT
        elif operation.state is ProviderOutcomeState.CANCELED:
            attempt.status = PaymentStatus.CANCELED
            sale.status = SaleStatus.DRAFT
        elif operation.state is ProviderOutcomeState.UNKNOWN:
            attempt.status = PaymentStatus.UNKNOWN
            sale.status = SaleStatus.PAYMENT_PENDING
        else:
            attempt.status = PaymentStatus.PENDING
            sale.status = SaleStatus.PAYMENT_PENDING
        attempt.updated_by_id = owner_id
        sale.updated_by_id = owner_id
        self._commit()
        logger.info(
            "Payment state sale=%s attempt=%s external=%s state=%s",
            sale.id,
            attempt.id,
            attempt.external_id,
            attempt.status.value,
        )
        if sale.status is SaleStatus.PAID:
            return self._advance_paid_sale(
                sale.id, owner_id=owner_id, evidence=operation.fiscal_evidence
            )
        return sale

    def _record_payment_error(
        self, start: CheckoutStart, *, owner_id: UUIDv7, error: ProviderCallError
    ) -> Sale:
        sale = self._locked_owned(start.sale_id, owner_id)
        attempt = self._payment_by_id(sale, start.attempt_id)
        attempt.status = PaymentStatus.UNKNOWN if error.outcome_unknown else PaymentStatus.FAILED
        attempt.error_code = error.code
        attempt.error_message = str(error)
        attempt.updated_by_id = owner_id
        sale.status = SaleStatus.PAYMENT_PENDING if error.outcome_unknown else SaleStatus.DRAFT
        sale.updated_by_id = owner_id
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
        attempt.status = PaymentStatus.UNKNOWN
        attempt.error_code = error.code
        attempt.error_message = str(error)
        sale.status = SaleStatus.PAYMENT_PENDING
        self._commit()
        return sale

    def _mark_payment_unknown_without_external_id(
        self, sale_id: UUIDv7, attempt_id: UUIDv7, owner_id: UUIDv7
    ) -> Sale:
        sale = self._locked_owned(sale_id, owner_id)
        attempt = self._payment_by_id(sale, attempt_id)
        attempt.status = PaymentStatus.UNKNOWN
        attempt.error_code = "missing_external_id"
        attempt.error_message = "Payment outcome is unknown; no provider operation ID is available."
        self._commit()
        return sale

    def _advance_paid_sale(
        self, sale_id: UUIDv7, *, owner_id: UUIDv7, evidence: object | None = None
    ) -> Sale:
        sale = self._locked_owned(sale_id, owner_id)
        if sale.status not in {SaleStatus.PAID, SaleStatus.FISCALIZATION_PENDING}:
            self._commit()
            return sale
        payment = self._successful_payment(sale)
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
        created = False
        if sale.fiscalization is None:
            fiscalization = Fiscalization(
                sale_id=sale.id,
                payment_attempt_id=payment.id,
                integration_id=self._binding.integration_id,
                provider=self._binding.provider,
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
            created = True
        sale.status = SaleStatus.FISCALIZATION_PENDING
        sale.updated_by_id = owner_id
        self._commit()
        if not created:
            return sale
        if evidence is None and payment.payment_method is PaymentMethod.CARD:
            if payment.external_id is None:
                return self._mark_fiscal_unknown(
                    sale.id, owner_id, "missing_payment_id", "Payment evidence is unavailable."
                )
            try:
                refreshed = self._binding.payment.get_payment(
                    payment.external_id, self._payment_request(sale, payment)
                )
            except ProviderCallError as exc:
                return self._mark_fiscal_unknown(sale.id, owner_id, exc.code, str(exc))
            evidence = refreshed.fiscal_evidence
        if evidence is None and payment.payment_method is PaymentMethod.CARD:
            return self._mark_fiscal_unknown(
                sale.id,
                owner_id,
                "missing_payment_evidence",
                "Fiscal payment evidence is unavailable.",
            )
        return self._initiate_fiscalization(sale.id, owner_id=owner_id, evidence=evidence)

    def _initiate_fiscalization(
        self, sale_id: UUIDv7, *, owner_id: UUIDv7, evidence: object | None = None
    ) -> Sale:
        sale = self._require_owned(sale_id, owner_id)
        fiscalization = sale.fiscalization
        payment = self._successful_payment(sale)
        if fiscalization is None:
            raise CheckoutStateError("Fiscalization record is missing.")
        if evidence is None and payment.payment_method is PaymentMethod.CARD:
            if payment.external_id is None:
                return self._mark_fiscal_unknown(
                    sale.id, owner_id, "missing_payment_id", "Payment evidence is unavailable."
                )
            try:
                payment_state = self._binding.payment.get_payment(
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
        try:
            operation = self._binding.fiscal.initiate_fiscalization(request)
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
        try:
            evidence: object | None = None
            if payment.payment_method is PaymentMethod.CARD:
                if payment.external_id is None:
                    return self._mark_fiscal_unknown(
                        sale.id,
                        owner_id,
                        "missing_payment_id",
                        "Payment evidence is unavailable.",
                    )
                payment_state = self._binding.payment.get_payment(
                    payment.external_id, self._payment_request(sale, payment)
                )
                if payment_state.fiscal_evidence is None:
                    raise ProviderCallError(
                        "missing_payment_evidence",
                        "Provider did not return fiscal payment evidence.",
                        outcome_unknown=True,
                    )
                evidence = payment_state.fiscal_evidence
            request = self._fiscal_request(sale, fiscalization, payment, evidence)
            operation = self._binding.fiscal.get_fiscalization(fiscalization.external_id, request)
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
        self._apply_common_operation(fiscalization, operation)
        if operation.state is ProviderOutcomeState.SUCCEEDED:
            fiscalization.status = FiscalizationStatus.SUCCEEDED
            fiscalization.external_receipt_id = operation.external_reference
            sale.status = SaleStatus.COMPLETED
            sale.completed_at = datetime.now(UTC)
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
        self._commit()
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
        fiscalization.status = (
            FiscalizationStatus.UNKNOWN if error.outcome_unknown else FiscalizationStatus.FAILED
        )
        fiscalization.error_code = error.code
        fiscalization.error_message = str(error)
        sale.status = (
            SaleStatus.FISCALIZATION_PENDING
            if error.outcome_unknown
            else SaleStatus.FISCALIZATION_FAILED
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
                    line_total=Decimal(str(allocation["unit_price"]))
                    * int(allocation["quantity"]),
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
        record.provider_metadata = dict(operation.metadata)
        record.error_code = operation.error_code
        record.error_message = operation.error_message

    def _ensure_binding_matches(self, sale: Sale, *, allow_without_attempt: bool = False) -> None:
        if not sale.payments and allow_without_attempt:
            return
        persisted_integration_id = (
            sale.fiscalization.integration_id
            if sale.fiscalization is not None
            else self._latest_payment(sale).integration_id
        )
        if (
            persisted_integration_id is not None
            and persisted_integration_id != self._binding.integration_id
        ):
            raise CheckoutProviderMismatchError

    @staticmethod
    def _latest_payment(sale: Sale) -> PaymentAttempt:
        if not sale.payments:
            raise CheckoutStateError("Payment attempt is missing.")
        return max(sale.payments, key=lambda item: item.attempt_number)

    @staticmethod
    def _successful_payment(sale: Sale) -> PaymentAttempt:
        attempt = next(
            (item for item in reversed(sale.payments) if item.status is PaymentStatus.SUCCEEDED),
            None,
        )
        if attempt is None:
            raise CheckoutStateError("Successful payment is missing.")
        return attempt

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
