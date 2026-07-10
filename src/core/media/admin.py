from __future__ import annotations

from sqladmin import ModelView

from core.media.models import Image, ImageLink


class ImageAdmin(ModelView, model=Image):
    """SQLAdmin view for image metadata records."""

    name = "Image"
    name_plural = "Images"
    icon = "fa-solid fa-image"
    column_list = [Image.original_filename, Image.mime_type, Image.size_bytes]
    column_searchable_list = [Image.original_filename, Image.checksum, Image.source_key]
    column_sortable_list = [Image.created_at, Image.original_filename]


class ImageLinkAdmin(ModelView, model=ImageLink):
    """SQLAdmin view for attaching images to catalog entities."""

    name = "Image link"
    name_plural = "Image links"
    icon = "fa-solid fa-link"
    column_list = [ImageLink.entity_type, ImageLink.entity_id, ImageLink.role, ImageLink.sort_order]
    column_sortable_list = [ImageLink.entity_type, ImageLink.role, ImageLink.sort_order]
