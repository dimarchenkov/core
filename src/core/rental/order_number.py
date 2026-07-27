class RentalOrderNumberGenerator:
    """Format stable, business-facing rental order numbers."""

    @staticmethod
    def generate(number: int) -> str:
        """Build an order number from a positive sequence value."""
        if number < 1:
            raise ValueError("Rental order number must be positive.")
        return f"RORD-{number:06d}"
