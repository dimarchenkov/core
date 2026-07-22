from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.activity.routes import router as activity_router
from core.admin import setup_admin
from core.catalog.routes import product_router, variant_router
from core.catalog.routes import router as catalog_router
from core.config import get_settings
from core.identity.routes import router as identity_router
from core.intake.routes import router as intake_router
from core.integrations.aqsi.routes import router as aqsi_router
from core.labels.routes import router as labels_router
from core.logging import configure_logging
from core.media.routes import image_link_router, image_router
from core.pricing.routes import router as pricing_router
from core.readiness.routes import router as readiness_router
from core.receipt.routes import router as receipt_router
from core.supplier.routes import router as supplier_router
from core.web.routes import router as web_router
from core.web.routes import static_root as web_static_root


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
    app.include_router(aqsi_router)
    app.include_router(labels_router)
    app.include_router(pricing_router)
    app.include_router(readiness_router)
    app.include_router(receipt_router)
    app.include_router(supplier_router)
    app.include_router(identity_router)
    app.include_router(activity_router)
    app.include_router(web_router)
    app.mount("/app/assets", StaticFiles(directory=web_static_root), name="workflow-assets")

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Report that the API process is running."""
        return {"status": "ok"}

    return app


app = create_app()
