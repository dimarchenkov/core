from __future__ import annotations

from fastapi import FastAPI
from sqladmin import Admin

from core.catalog.admin import CategoryAdmin
from core.database import engine


def setup_admin(app: FastAPI) -> Admin:
    """Attach SQLAdmin to the FastAPI app for infrastructure validation."""
    admin = Admin(app, engine)
    admin.add_view(CategoryAdmin)
    return admin
