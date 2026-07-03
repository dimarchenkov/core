from __future__ import annotations

from fastapi import FastAPI
from sqladmin import Admin

from core.database import engine


def setup_admin(app: FastAPI) -> Admin:
    """Attach SQLAdmin to the FastAPI app for infrastructure validation."""
    return Admin(app, engine)
