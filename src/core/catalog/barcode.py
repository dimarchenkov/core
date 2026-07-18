from __future__ import annotations


class InternalBarcodeGenerator:
    """Generate EAN-13-compatible restricted-circulation numbers for Core."""

    PREFIX = "20"
    ITEM_DIGITS = 10
    MAX_NUMBER = (10**ITEM_DIGITS) - 1

    @classmethod
    def generate(cls, number: int) -> str:
        """Return a 13-digit internal barcode with a valid EAN check digit."""
        if number <= 0:
            msg = "Barcode sequence number must be positive."
            raise ValueError(msg)
        if number > cls.MAX_NUMBER:
            msg = "Barcode sequence number exceeds the internal EAN-13 capacity."
            raise ValueError(msg)

        body = f"{cls.PREFIX}{number:0{cls.ITEM_DIGITS}d}"
        check_digit = cls.calculate_check_digit(body)
        return f"{body}{check_digit}"

    @staticmethod
    def calculate_check_digit(body: str) -> int:
        """Calculate an EAN-13 check digit from exactly twelve numeric digits."""
        if len(body) != 12 or not body.isdigit():
            msg = "EAN-13 body must contain exactly twelve digits."
            raise ValueError(msg)
        weighted_sum = sum(
            int(digit) * (1 if position % 2 == 1 else 3)
            for position, digit in enumerate(body, start=1)
        )
        return (10 - (weighted_sum % 10)) % 10
