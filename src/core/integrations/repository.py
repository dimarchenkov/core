from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.integrations.enums import CredentialKind, IntegrationProvider
from core.integrations.models import Integration, IntegrationCredential
from core.shared.db import UUIDv7


class IntegrationRepository:
    """Database access for provider connections and their encrypted credentials."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the current unit of work."""
        self._session = session

    def add(self, integration: Integration) -> Integration:
        """Add one provider connection without enforcing provider singleton semantics."""
        self._session.add(integration)
        return integration

    def get(self, integration_id: UUIDv7, *, for_update: bool = False) -> Integration | None:
        """Return an active integration by ID, optionally locking it."""
        statement = select(Integration).where(
            Integration.id == integration_id,
            Integration.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_for_provider(self, provider: IntegrationProvider) -> Sequence[Integration]:
        """Return all non-deleted connections for a provider in deterministic order."""
        statement = (
            select(Integration)
            .where(
                Integration.provider == provider,
                Integration.deleted_at.is_(None),
            )
            .order_by(Integration.created_at, Integration.id)
        )
        return self._session.scalars(statement).all()

    def get_credential(
        self,
        integration_id: UUIDv7,
        kind: CredentialKind,
        *,
        for_update: bool = False,
    ) -> IntegrationCredential | None:
        """Return credential ciphertext and metadata without decrypting it."""
        statement = select(IntegrationCredential).where(
            IntegrationCredential.integration_id == integration_id,
            IntegrationCredential.kind == kind,
            IntegrationCredential.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add_credential(self, credential: IntegrationCredential) -> IntegrationCredential:
        """Add an encrypted credential to the current unit of work."""
        self._session.add(credential)
        return credential
