from __future__ import annotations


class SupplierCodeGenerator:
    """Format stable, system-generated supplier reference codes."""

    @staticmethod
    def generate(number: int) -> str:
        """Build a supplier code from a positive sequence number."""
        if number < 1:
            raise ValueError("Supplier code number must be positive.")
        return f"SUP-{number:06d}"
