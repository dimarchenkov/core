class CustomerDomainError(Exception):
    """Base class for customer business rule violations."""


class CustomerValidationError(CustomerDomainError):
    """Raised when customer contact data violates domain rules."""


class CustomerNameRequiredError(CustomerValidationError):
    """Raised when a customer name is empty after normalization."""


class CustomerPhoneInvalidError(CustomerValidationError):
    """Raised when a phone cannot be normalized into a usable value."""


class CustomerEmailInvalidError(CustomerValidationError):
    """Raised when a non-empty email has an invalid shape."""


class CustomerNumberInvalidError(CustomerValidationError):
    """Raised when a stable customer number is missing."""


class CustomerNotFoundError(CustomerDomainError):
    """Raised when a customer does not exist."""


class CustomerStateError(CustomerDomainError):
    """Raised when a lifecycle command is invalid for the current state."""
