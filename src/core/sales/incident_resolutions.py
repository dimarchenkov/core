from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.sales.enums import (
    FinancialActorType,
    FinancialEntityType,
    FinancialEventType,
    FiscalizationStatus,
    FiscalVerificationOutcome,
    IncidentKind,
    IncidentResolutionStatus,
    IncidentResolutionType,
    PaymentMethod,
    PaymentStatus,
)
from core.sales.financial_events import append_financial_event
from core.sales.models import (
    FinancialIncidentResolution,
    PaymentAttempt,
    Sale,
)
from core.sales.repository import SaleRepository
from core.shared.db import UUIDv7


class IncidentResolutionError(Exception):
    """Reject an unsafe or unsupported financial incident conclusion."""


class IncidentResolutionNotFoundError(IncidentResolutionError):
    """Hide financial records that are not owned by the current operator."""


class IncidentResolutionConflictError(IncidentResolutionError):
    """Reject a conclusion that conflicts with durable provider or review facts."""


class IncidentResolutionService:
    """Resolve payment and fiscal review tracks without creating financial operations."""

    def __init__(self, session: Session) -> None:
        """Bind incident resolution commands to one transaction-scoped session."""
        self._session = session
        self._sales = SaleRepository(session)

    def confirm_single_charge(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        confirmation: bool,
        evidence_reference: str,
        operator_note: str | None = None,
    ) -> FinancialIncidentResolution:
        """Close a duplicate-risk track after an operator verifies one bank charge."""
        if not confirmation:
            raise IncidentResolutionConflictError(
                "Подтвердите, что вы сверили операции AQSI и нашли одно списание."
            )
        evidence = evidence_reference.strip()
        if len(evidence) < 3:
            raise IncidentResolutionConflictError("Укажите основание сверки оплаты.")
        sale = self._locked_owned(sale_id, owner_id)
        payment = self._duplicate_risk_payment(sale)
        if payment is None:
            raise IncidentResolutionConflictError(
                "У продажи нет неразрешённого риска двойного списания."
            )
        existing = self._sales.get_incident_resolution_for_update(
            incident_kind=IncidentKind.PAYMENT_DUPLICATE_RISK,
            incident_id=payment.id,
        )
        if existing is not None:
            if (
                existing.resolution_type
                is IncidentResolutionType.SINGLE_CHARGE_OPERATOR_CONFIRMED
                and existing.resolution_status is IncidentResolutionStatus.RESOLVED
            ):
                self._session.commit()
                return existing
            if existing.resolution_status is IncidentResolutionStatus.RESOLVED:
                raise IncidentResolutionConflictError("Инцидент оплаты уже разрешён иначе.")
        if payment.status is not PaymentStatus.SUCCEEDED or not payment.external_id:
            raise IncidentResolutionConflictError(
                "Исходная операция не имеет подтверждённого результата SUCCEEDED."
            )
        provider_successes = [
            item
            for item in sale.payments
            if item.payment_method is PaymentMethod.CARD
            and item.provider is not None
            and item.status is PaymentStatus.SUCCEEDED
        ]
        if len(provider_successes) != 1:
            raise IncidentResolutionConflictError(
                "Известно несколько успешных списаний. Требуется отдельный инцидент возврата."
            )
        if not any(item.emergency_fallback for item in sale.payments):
            raise IncidentResolutionConflictError("Аварийная запись оплаты не найдена.")

        now = datetime.now(UTC)
        if existing is None:
            resolution = FinancialIncidentResolution(
                sale_id=sale.id,
                incident_id=payment.id,
                incident_kind=IncidentKind.PAYMENT_DUPLICATE_RISK,
                resolution_type=IncidentResolutionType.SINGLE_CHARGE_OPERATOR_CONFIRMED,
                resolution_status=IncidentResolutionStatus.RESOLVED,
                resolved_by_id=owner_id,
                resolved_at=now,
                evidence_reference=evidence,
                operator_note=self._clean_optional(operator_note),
                created_by_id=owner_id,
                updated_by_id=owner_id,
            )
            sale.incident_resolutions.append(resolution)
            self._sales.add_incident_resolution(resolution)
        else:
            resolution = existing
            resolution.resolution_type = (
                IncidentResolutionType.SINGLE_CHARGE_OPERATOR_CONFIRMED
            )
            resolution.resolution_status = IncidentResolutionStatus.RESOLVED
            resolution.resolved_by_id = owner_id
            resolution.resolved_at = now
            resolution.evidence_reference = evidence
            resolution.operator_note = self._clean_optional(operator_note)
            resolution.updated_by_id = owner_id
        self._session.flush()
        correlation_id = payment.job.correlation_id if payment.job else f"payment:{payment.id}"
        common = {
            "sale_id": sale.id,
            "entity_type": FinancialEntityType.RECONCILIATION,
            "entity_id": payment.id,
            "correlation_id": correlation_id,
            "actor_type": FinancialActorType.OPERATOR,
            "actor_id": owner_id,
            "provider": payment.provider,
            "provider_operation_id": payment.external_id,
            "metadata": {
                "resolution_id": str(resolution.id),
                "evidence_reference": evidence,
            },
        }
        append_financial_event(
            self._session,
            event_type=FinancialEventType.PAYMENT_INCIDENT_REVIEWED,
            previous_status="duplicate_payment_risk",
            new_status="reviewed",
            safe_message="Оператор сверил операции эквайринга.",
            **common,
        )
        append_financial_event(
            self._session,
            event_type=FinancialEventType.SINGLE_CHARGE_OPERATOR_CONFIRMED,
            previous_status="reviewed",
            new_status="single_charge_confirmed",
            safe_message="Оператор подтвердил одно банковское списание.",
            **common,
        )
        append_financial_event(
            self._session,
            event_type=FinancialEventType.PAYMENT_INCIDENT_RESOLVED,
            previous_status="duplicate_payment_risk",
            new_status=IncidentResolutionStatus.RESOLVED.value,
            safe_message="Инцидент оплаты разрешён по результатам ручной сверки.",
            **common,
        )
        self._session.commit()
        return resolution

    def record_payment_review(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        resolution_type: IncidentResolutionType,
        confirmation: bool,
        operator_note: str | None = None,
    ) -> FinancialIncidentResolution:
        """Record emergency-payment evidence without changing provider status."""
        if not confirmation:
            raise IncidentResolutionConflictError("Подтвердите результат ручной проверки.")
        allowed = {
            IncidentResolutionType.EXTERNAL_POS_PAYMENT_CONFIRMED,
            IncidentResolutionType.ORIGINAL_OPERATION_CANCELED_CONFIRMED,
            IncidentResolutionType.MANUAL_REVIEW_CONFIRMED,
        }
        if resolution_type not in allowed:
            raise IncidentResolutionConflictError("Неподдерживаемый результат проверки оплаты.")
        sale = self._locked_owned(sale_id, owner_id)
        fallback = next((item for item in reversed(sale.payments) if item.emergency_fallback), None)
        payment = self._emergency_original_payment(sale, fallback)
        if fallback is None or payment is None:
            raise IncidentResolutionConflictError("Аварийная операция оплаты не найдена.")
        if (
            resolution_type
            is IncidentResolutionType.ORIGINAL_OPERATION_CANCELED_CONFIRMED
            and payment.status is not PaymentStatus.CANCELED
        ):
            raise IncidentResolutionConflictError(
                "Провайдер ещё не подтвердил финальную отмену исходной операции."
            )
        resolution_status = (
            IncidentResolutionStatus.RESOLVED
            if resolution_type
            is IncidentResolutionType.ORIGINAL_OPERATION_CANCELED_CONFIRMED
            else IncidentResolutionStatus.UNKNOWN
            if resolution_type is IncidentResolutionType.MANUAL_REVIEW_CONFIRMED
            else IncidentResolutionStatus.REQUIRES_ACTION
        )
        if (
            resolution_type is IncidentResolutionType.EXTERNAL_POS_PAYMENT_CONFIRMED
            and payment.status is PaymentStatus.CANCELED
        ):
            resolution_status = IncidentResolutionStatus.RESOLVED
        existing = self._sales.get_incident_resolution_for_update(
            incident_kind=IncidentKind.PAYMENT_DUPLICATE_RISK,
            incident_id=payment.id,
        )
        if existing is not None:
            if (
                existing.resolution_type is resolution_type
                and existing.resolution_status is resolution_status
            ):
                self._session.commit()
                return existing
            if existing.resolution_status is IncidentResolutionStatus.RESOLVED:
                raise IncidentResolutionConflictError("Инцидент оплаты уже разрешён.")
            resolution = existing
            resolution.resolution_type = resolution_type
            resolution.resolution_status = resolution_status
            resolution.resolved_by_id = owner_id
            resolution.resolved_at = datetime.now(UTC)
            resolution.evidence_reference = "operator-confirmed-emergency-review"
            resolution.operator_note = self._clean_optional(operator_note)
            resolution.updated_by_id = owner_id
        else:
            resolution = FinancialIncidentResolution(
                sale_id=sale.id,
                incident_id=payment.id,
                incident_kind=IncidentKind.PAYMENT_DUPLICATE_RISK,
                resolution_type=resolution_type,
                resolution_status=resolution_status,
                resolved_by_id=owner_id,
                resolved_at=datetime.now(UTC),
                evidence_reference="operator-confirmed-emergency-review",
                operator_note=self._clean_optional(operator_note),
                created_by_id=owner_id,
                updated_by_id=owner_id,
            )
            sale.incident_resolutions.append(resolution)
            self._sales.add_incident_resolution(resolution)
        self._session.flush()
        append_financial_event(
            self._session,
            sale_id=sale.id,
            entity_type=FinancialEntityType.RECONCILIATION,
            entity_id=payment.id,
            event_type=(
                FinancialEventType.PAYMENT_INCIDENT_RESOLVED
                if resolution_status is IncidentResolutionStatus.RESOLVED
                else FinancialEventType.PAYMENT_INCIDENT_REVIEWED
            ),
            correlation_id=(
                payment.job.correlation_id if payment.job else f"payment:{payment.id}"
            ),
            actor_type=FinancialActorType.OPERATOR,
            actor_id=owner_id,
            previous_status=payment.status.value,
            new_status=resolution_status.value,
            provider=payment.provider,
            provider_operation_id=payment.external_id,
            safe_message={
                IncidentResolutionType.EXTERNAL_POS_PAYMENT_CONFIRMED: (
                    "Оператор подтвердил оплату на физической кассе; исходная операция "
                    "остаётся на сверке до финального статуса."
                ),
                IncidentResolutionType.ORIGINAL_OPERATION_CANCELED_CONFIRMED: (
                    "Оператор подтвердил проверку финального статуса отмены."
                ),
                IncidentResolutionType.MANUAL_REVIEW_CONFIRMED: (
                    "Оператор записал результат ручной проверки без изменения фактов провайдера."
                ),
            }[resolution_type],
            metadata={
                "resolution_id": str(resolution.id),
                "resolution_type": resolution_type.value,
            },
        )
        self._session.commit()
        return resolution

    def record_fiscal_verification(
        self,
        sale_id: UUIDv7,
        *,
        owner_id: UUIDv7,
        outcome: FiscalVerificationOutcome,
        confirmation: bool,
        evidence_reference: str | None = None,
        operator_note: str | None = None,
        fiscal_fn: str | None = None,
        fiscal_fd: str | None = None,
        fiscal_fpd: str | None = None,
    ) -> FinancialIncidentResolution:
        """Record receipt evidence or its absence without issuing a fiscal command."""
        if not confirmation:
            raise IncidentResolutionConflictError("Подтвердите результат проверки чека.")
        sale = self._locked_owned(sale_id, owner_id)
        fallback = next((item for item in reversed(sale.payments) if item.emergency_fallback), None)
        fiscal = sale.fiscalization
        if fallback is None or fiscal is None or fiscal.status is not FiscalizationStatus.SKIPPED:
            raise IncidentResolutionConflictError(
                "Для продажи нет операторского утверждения о внешнем чеке."
            )
        evidence = self._clean_optional(evidence_reference)
        note = self._clean_optional(operator_note)
        fn = self._clean_optional(fiscal_fn)
        fd = self._clean_optional(fiscal_fd)
        fpd = self._clean_optional(fiscal_fpd)
        if outcome is FiscalVerificationOutcome.VERIFIED and not any(
            (evidence, fn, fd, fpd)
        ):
            raise IncidentResolutionConflictError(
                "Для подтверждения чека укажите ссылку или фискальные реквизиты."
            )
        if outcome is not FiscalVerificationOutcome.VERIFIED and not note:
            raise IncidentResolutionConflictError(
                "Добавьте комментарий с результатом проверки чека."
            )

        resolution_type, resolution_status, event_type, message = {
            FiscalVerificationOutcome.VERIFIED: (
                IncidentResolutionType.FISCAL_RECEIPT_VERIFIED,
                IncidentResolutionStatus.RESOLVED,
                FinancialEventType.FISCAL_RECEIPT_VERIFIED,
                "Фискальный чек проверен оператором по внешним реквизитам.",
            ),
            FiscalVerificationOutcome.MISSING: (
                IncidentResolutionType.FISCAL_RECEIPT_MISSING,
                IncidentResolutionStatus.REQUIRES_ACTION,
                FinancialEventType.FISCAL_RECEIPT_MISSING_REPORTED,
                "Оператор подтвердил, что найден только эквайринговый слип; "
                "фискальный чек отсутствует.",
            ),
            FiscalVerificationOutcome.UNKNOWN: (
                IncidentResolutionType.FISCAL_VERIFICATION_UNKNOWN,
                IncidentResolutionStatus.UNKNOWN,
                FinancialEventType.FISCAL_VERIFICATION_UNKNOWN_RECORDED,
                "Результат проверки фискального чека установить не удалось.",
            ),
        }[outcome]
        existing = self._sales.get_incident_resolution_for_update(
            incident_kind=IncidentKind.FISCAL_RECEIPT_VERIFICATION,
            incident_id=fiscal.id,
        )
        if existing is not None:
            if existing.resolution_type is resolution_type:
                self._session.commit()
                return existing
            if existing.resolution_type is IncidentResolutionType.FISCAL_RECEIPT_VERIFIED:
                raise IncidentResolutionConflictError("Фискальный чек уже подтверждён.")
            if (
                existing.resolution_type is IncidentResolutionType.FISCAL_RECEIPT_MISSING
                and resolution_type is IncidentResolutionType.FISCAL_VERIFICATION_UNKNOWN
            ):
                raise IncidentResolutionConflictError(
                    "Подтверждённое отсутствие чека нельзя заменить неизвестным результатом."
                )
            previous_status = existing.resolution_status.value
            resolution = existing
            resolution.resolution_type = resolution_type
            resolution.resolution_status = resolution_status
            resolution.resolved_by_id = owner_id
            resolution.resolved_at = datetime.now(UTC)
            resolution.evidence_reference = evidence
            resolution.operator_note = note
            resolution.fiscal_fn = fn
            resolution.fiscal_fd = fd
            resolution.fiscal_fpd = fpd
            resolution.updated_by_id = owner_id
        else:
            previous_status = "operator_reported"
            resolution = FinancialIncidentResolution(
                sale_id=sale.id,
                incident_id=fiscal.id,
                incident_kind=IncidentKind.FISCAL_RECEIPT_VERIFICATION,
                resolution_type=resolution_type,
                resolution_status=resolution_status,
                resolved_by_id=owner_id,
                resolved_at=datetime.now(UTC),
                evidence_reference=evidence,
                operator_note=note,
                fiscal_fn=fn,
                fiscal_fd=fd,
                fiscal_fpd=fpd,
                created_by_id=owner_id,
                updated_by_id=owner_id,
            )
            sale.incident_resolutions.append(resolution)
            self._sales.add_incident_resolution(resolution)
        self._session.flush()
        append_financial_event(
            self._session,
            sale_id=sale.id,
            entity_type=FinancialEntityType.RECONCILIATION,
            entity_id=fiscal.id,
            event_type=event_type,
            correlation_id=f"fiscal-verification:{fiscal.id}",
            actor_type=FinancialActorType.OPERATOR,
            actor_id=owner_id,
            previous_status=previous_status,
            new_status=resolution_status.value,
            provider=fiscal.provider,
            provider_operation_id=fiscal.external_id,
            safe_message=message,
            metadata={
                "resolution_id": str(resolution.id),
                "evidence_reference": evidence,
                "fiscal_fn": fn,
                "fiscal_fd": fd,
                "fiscal_fpd": fpd,
            },
        )
        self._session.commit()
        return resolution

    def _locked_owned(self, sale_id: UUIDv7, owner_id: UUIDv7) -> Sale:
        sale = self._sales.get_owned_for_update(sale_id, owner_id)
        if sale is None:
            raise IncidentResolutionNotFoundError("Продажа не найдена.")
        return sale

    @staticmethod
    def _duplicate_risk_payment(sale: Sale) -> PaymentAttempt | None:
        return next(
            (item for item in reversed(sale.payments) if item.duplicate_payment_risk),
            None,
        )

    @staticmethod
    def _emergency_original_payment(
        sale: Sale, fallback: PaymentAttempt | None
    ) -> PaymentAttempt | None:
        if fallback is None:
            return None
        linked = fallback.linked_payment_attempt_id
        return next(
            (
                item
                for item in reversed(sale.payments)
                if not item.emergency_fallback
                and item.accounting_superseded
                and (linked is None or str(item.id) == linked)
            ),
            None,
        )

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        cleaned = value.strip() if value else ""
        return cleaned or None
