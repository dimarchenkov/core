from __future__ import annotations

import logging


def configure_logging(log_level: str) -> None:
    """Configure application logging for API and worker processes."""
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
