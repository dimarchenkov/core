from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from core.integrations.enums import IntegrationProvider
from core.sales.enums import FinancialActorType, FinancialEntityType, FinancialEventType
from core.sales.models import FinancialOperationEvent
from core.shared.db import UUIDv7


def append_financial_event(
    session: Session,
    *,
    sale_id: UUIDv7,
    entity_type: FinancialEntityType,
    entity_id: UUIDv7,
    event_type: FinancialEventType,
    correlation_id: str,
    actor_type: FinancialActorType,
    actor_id: UUIDv7 | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
    provider: IntegrationProvider | None = None,
    provider_operation_id: str | None = None,
    error_code: str | None = None,
    safe_message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> FinancialOperationEvent:
    """Stage one sanitized append-only event in the current DB transaction."""
    event = FinancialOperationEvent(
        sale_id=sale_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        previous_status=previous_status,
        new_status=new_status,
        provider=provider,
        provider_operation_id=provider_operation_id,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id[:255],
        error_code=error_code[:128] if error_code else None,
        safe_message=safe_message[:1000] if safe_message else None,
        metadata_json=metadata or {},
        occurred_at=datetime.now(UTC),
    )
    session.add(event)
    return event


def list_financial_events(
    session: Session,
    *,
    sale_id: UUIDv7,
    limit: int = 100,
    before: datetime | None = None,
) -> Sequence[FinancialOperationEvent]:
    """Return a bounded newest-first timeline for one already-authorized Sale."""
    statement = select(FinancialOperationEvent).where(FinancialOperationEvent.sale_id == sale_id)
    if before is not None:
        statement = statement.where(FinancialOperationEvent.occurred_at < before)
    return session.scalars(
        statement.order_by(
            FinancialOperationEvent.occurred_at.desc(),
            FinancialOperationEvent.id.desc(),
        ).limit(limit)
    ).all()


def financial_event_exists(
    session: Session,
    *,
    correlation_id: str,
    event_type: FinancialEventType,
) -> bool:
    """Check whether an idempotent operational marker already exists."""
    return bool(
        session.scalar(
            select(
                exists().where(
                    FinancialOperationEvent.correlation_id == correlation_id,
                    FinancialOperationEvent.event_type == event_type,
                )
            )
        )
    )
