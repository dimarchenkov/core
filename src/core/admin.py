from __future__ import annotations

from fastapi import FastAPI
from sqladmin import Admin

from core.catalog.admin import CatalogProductAdmin, CatalogVariantAdmin, CategoryAdmin
from core.database import engine
from core.identity.admin import PrivilegeAuditEventAdmin, UserAdmin
from core.integrations.aqsi.admin import PublicationAdmin, PublicationAttemptAdmin
from core.media.admin import ImageAdmin, ImageLinkAdmin
from core.pricing.admin import PriceAdmin
from core.receipt.admin import ReceiptAdmin, ReceiptItemAdmin
from core.supplier.admin import SupplierAdmin


def setup_admin(app: FastAPI) -> Admin:
    """Attach SQLAdmin to the FastAPI app for infrastructure validation."""
    admin = Admin(app, engine)
    admin.add_view(CategoryAdmin)
    admin.add_view(CatalogProductAdmin)
    admin.add_view(CatalogVariantAdmin)
    admin.add_view(ImageAdmin)
    admin.add_view(ImageLinkAdmin)
    admin.add_view(PriceAdmin)
    admin.add_view(PublicationAdmin)
    admin.add_view(PublicationAttemptAdmin)
    admin.add_view(UserAdmin)
    admin.add_view(PrivilegeAuditEventAdmin)
    admin.add_view(ReceiptAdmin)
    admin.add_view(ReceiptItemAdmin)
    admin.add_view(SupplierAdmin)
    return admin
