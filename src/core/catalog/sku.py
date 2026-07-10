from __future__ import annotations


class SkuGenerator:
    """Build stable catalog variant SKU values from reserved sequence numbers."""

    @staticmethod
    def generate(number: int) -> str:
        """Format a positive reserved number as an MVP catalog SKU."""
        if number < 1:
            raise ValueError("SKU number must be positive.")
        return f"SKU-{number:06d}"
