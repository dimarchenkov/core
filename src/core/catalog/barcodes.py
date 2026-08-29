from __future__ import annotations

from enum import StrEnum


class BarcodeSource(StrEnum):
    """Business origin of a machine-readable Variant identifier."""

    INTERNAL = "internal"
    MANUFACTURER = "manufacturer"


class BarcodeFormat(StrEnum):
    """Production formats accepted by the first multi-barcode workflow."""

    EAN_13 = "ean_13"
    EAN_8 = "ean_8"
    UPC_A = "upc_a"
    CODE_128 = "code_128"


class BarcodeValidationError(ValueError):
    """Raised when scanner input cannot be a supported stable barcode."""


def normalize_barcode(value: str) -> str:
    """Remove scanner suffix/control characters and validate the resulting value."""
    normalized = value.strip()
    if not normalized:
        raise BarcodeValidationError("Barcode is required.")
    if len(normalized) > 128:
        raise BarcodeValidationError("Barcode cannot exceed 128 characters.")
    if any(ord(character) < 32 or ord(character) > 126 for character in normalized):
        raise BarcodeValidationError("Code 128 barcode must contain printable ASCII characters.")
    detect_barcode_format(normalized)
    return normalized


def detect_barcode_format(value: str) -> BarcodeFormat:
    """Detect GTIN formats first and otherwise accept printable Code 128 data."""
    if value.isdigit() and len(value) in {8, 12, 13}:
        barcode_format = {
            8: BarcodeFormat.EAN_8,
            12: BarcodeFormat.UPC_A,
            13: BarcodeFormat.EAN_13,
        }[len(value)]
        if calculate_gtin_check_digit(value[:-1]) != int(value[-1]):
            raise BarcodeValidationError(f"{barcode_format.value} check digit is invalid.")
        return barcode_format
    return BarcodeFormat.CODE_128


def calculate_gtin_check_digit(body: str) -> int:
    """Calculate the GS1 modulo-10 check digit for EAN-8, UPC-A, or EAN-13."""
    if not body.isdigit() or len(body) not in {7, 11, 12}:
        raise BarcodeValidationError("GTIN body must contain 7, 11, or 12 digits.")
    weighted = sum(
        int(digit) * (3 if position % 2 == 0 else 1)
        for position, digit in enumerate(reversed(body))
    )
    return (10 - weighted % 10) % 10


def is_aqsi_compatible(value: str) -> bool:
    """Return whether AQSI's current numeric 4-22 barcode contract accepts a value."""
    return value.isdigit() and 4 <= len(value) <= 22
