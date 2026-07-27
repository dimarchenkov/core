from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from core.customers.enums import CustomerStatus
from core.customers.exceptions import (
    CustomerEmailInvalidError,
    CustomerNameRequiredError,
    CustomerNumberInvalidError,
    CustomerPhoneInvalidError,
    CustomerStateError,
)
from core.shared.db import UUIDv7

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(slots=True, init=False)
class Customer:
    """Customer aggregate owning current contacts and activation lifecycle."""

    _id: UUIDv7
    _customer_number: str
    _full_name: str
    _phone: str
    _email: str | None
    _note: str | None
    _status: CustomerStatus
    _created_at: datetime
    _updated_at: datetime

    def __init__(
        self,
        id: UUIDv7,
        customer_number: str,
        full_name: str,
        phone: str,
        email: str | None,
        note: str | None,
        status: CustomerStatus,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        """Rehydrate controlled aggregate state from persistence."""
        self._id = id
        self._customer_number = customer_number
        self._full_name = full_name
        self._phone = phone
        self._email = email
        self._note = note
        self._status = status
        self._created_at = created_at
        self._updated_at = updated_at

    @property
    def id(self) -> UUIDv7:
        """Return the immutable technical identifier."""
        return self._id

    @property
    def customer_number(self) -> str:
        """Return the immutable business-facing customer number."""
        return self._customer_number

    @property
    def full_name(self) -> str:
        """Return the current normalized customer name."""
        return self._full_name

    @property
    def phone(self) -> str:
        """Return the current normalized phone."""
        return self._phone

    @property
    def email(self) -> str | None:
        """Return the current normalized email, when supplied."""
        return self._email

    @property
    def note(self) -> str | None:
        """Return the current internal note."""
        return self._note

    @property
    def status(self) -> CustomerStatus:
        """Return whether this customer may enter new business operations."""
        return self._status

    @property
    def created_at(self) -> datetime:
        """Return aggregate creation time."""
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        """Return the time of the latest successful domain change."""
        return self._updated_at

    @classmethod
    def create(
        cls,
        *,
        customer_id: UUIDv7,
        customer_number: str,
        full_name: str,
        phone: str,
        email: str | None = None,
        note: str | None = None,
        created_at: datetime | None = None,
    ) -> Customer:
        """Create an active customer with normalized current contact data."""
        normalized_number = customer_number.strip()
        if not normalized_number:
            raise CustomerNumberInvalidError
        timestamp = created_at or datetime.now(UTC)
        return cls(
            id=customer_id,
            customer_number=normalized_number,
            full_name=cls._normalize_name(full_name),
            phone=cls.normalize_phone(phone),
            email=cls._normalize_email(email),
            note=cls._normalize_optional(note),
            status=CustomerStatus.ACTIVE,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def rename(self, full_name: str) -> None:
        """Change the current name without rewriting order snapshots."""
        self._full_name = self._normalize_name(full_name)
        self._touch()

    def change_phone(self, phone: str) -> None:
        """Change the current normalized phone without rewriting history."""
        self._phone = self.normalize_phone(phone)
        self._touch()

    def change_email(self, email: str | None) -> None:
        """Set or clear the current email address."""
        self._email = self._normalize_email(email)
        self._touch()

    def update_note(self, note: str | None) -> None:
        """Set or clear the internal customer note."""
        self._note = self._normalize_optional(note)
        self._touch()

    def activate(self) -> None:
        """Allow an inactive customer to enter new business operations."""
        if self._status is CustomerStatus.ACTIVE:
            raise CustomerStateError("Customer is already active.")
        self._status = CustomerStatus.ACTIVE
        self._touch()

    def deactivate(self) -> None:
        """Exclude an active customer from new operations without deletion."""
        if self._status is CustomerStatus.INACTIVE:
            raise CustomerStateError("Customer is already inactive.")
        self._status = CustomerStatus.INACTIVE
        self._touch()

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """Normalize Russian local forms and international numbers for search."""
        raw = phone.strip()
        digits = "".join(character for character in raw if character.isdigit())
        if len(digits) == 10:
            digits = f"7{digits}"
        elif len(digits) == 11 and digits.startswith("8"):
            digits = f"7{digits[1:]}"
        if not 7 <= len(digits) <= 15:
            raise CustomerPhoneInvalidError
        return f"+{digits}"

    @staticmethod
    def _normalize_name(full_name: str) -> str:
        """Trim and collapse whitespace in the required customer name."""
        normalized = " ".join(full_name.split())
        if not normalized:
            raise CustomerNameRequiredError
        return normalized

    @staticmethod
    def _normalize_email(email: str | None) -> str | None:
        """Normalize an optional email and reject malformed non-empty values."""
        normalized = Customer._normalize_optional(email)
        if normalized is None:
            return None
        normalized = normalized.casefold()
        if _EMAIL_PATTERN.fullmatch(normalized) is None:
            raise CustomerEmailInvalidError
        return normalized

    @staticmethod
    def _normalize_optional(value: str | None) -> str | None:
        """Trim an optional string and represent blank content as null."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _touch(self) -> None:
        """Record a successful domain change."""
        self._updated_at = datetime.now(UTC)
