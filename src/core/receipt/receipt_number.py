from __future__ import annotations


class ReceiptNumberGenerator:
    """Format stable, system-generated receipt reference numbers."""

    @staticmethod
    def generate(number: int) -> str:
        """Build a receipt number from a positive sequence number."""
        if number < 1:
            raise ValueError("Receipt number must be positive.")
        return f"REC-{number:06d}"
