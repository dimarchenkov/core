from enum import StrEnum


class IntegrationProvider(StrEnum):
    """External providers supported by the integration boundary."""

    AQSI = "aqsi"


class ProviderCapability(StrEnum):
    """Independent business roles an external provider may implement."""

    PAYMENT = "payment"
    FISCALIZATION = "fiscalization"
    CATALOG_PROJECTION = "catalog_projection"
    EXTERNAL_SALES_IMPORT = "external_sales_import"
    PAYMENT_CANCELLATION = "payment_cancellation"
    REFUNDS = "refunds"
    FISCAL_RECEIPT_DETAILS = "fiscal_receipt_details"


class CredentialKind(StrEnum):
    """Secret kinds stored for an integration without exposing their values."""

    API_KEY = "api_key"
