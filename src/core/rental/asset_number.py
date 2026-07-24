class RentalAssetNumberGenerator:
    """Format reserved sequence values as stable employee-facing asset numbers."""

    PREFIX = "RENT-"

    @classmethod
    def generate(cls, number: int) -> str:
        """Return the canonical number for one positive reserved sequence value."""
        if number < 1:
            raise ValueError("Rental asset number must be positive.")
        return f"{cls.PREFIX}{number:06d}"
