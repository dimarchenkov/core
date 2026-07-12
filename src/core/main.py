from __future__ import annotations

from fastapi import FastAPI

from core.admin import setup_admin
from core.catalog.routes import product_router, variant_router
from core.catalog.routes import router as catalog_router
from core.config import get_settings
from core.identity.routes import router as identity_router
from core.intake.routes import router as intake_router
from core.logging import configure_logging
from core.media.routes import image_link_router, image_router


def create_app() -> FastAPI:
    """Create the Core FastAPI application with infrastructure integrations."""
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Core",
        description="Internal product, inventory, and rental system for 2010shop.",
        debug=settings.debug,
    )
    setup_admin(app)
    app.include_router(catalog_router)
    app.include_router(product_router)
    app.include_router(variant_router)
    app.include_router(image_router)
    app.include_router(image_link_router)
    app.include_router(intake_router)
    app.include_router(identity_router)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Report that the API process is running."""
        return {"status": "ok"}

    return app


app = create_app()
