class CustomerNumberGenerator:
    """Format stable, business-facing customer numbers."""

    @staticmethod
    def generate(number: int) -> str:
        """Build a customer number from a positive sequence value."""
        if number < 1:
            raise ValueError("Customer number must be positive.")
        return f"CUS-{number:06d}"
