from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from core.catalog.models import Category
from core.catalog.repository import CategoryRepository
from core.catalog.schemas import CategoryCreate, CategoryUpdate
from core.shared.db import UUIDv7


class CategoryNotFoundError(Exception):
    """Raised when a category cannot be found."""


class CategorySlugAlreadyExistsError(Exception):
    """Raised when a category slug is already used by another category."""


class CategoryParentError(Exception):
    """Raised when a category parent is invalid."""


class CategoryService:
    """Business operations for catalog categories."""

    def __init__(self, session: Session) -> None:
        """Create a service using the given database session."""
        self._session = session
        self._repository = CategoryRepository(session)

    def list_categories(self) -> Sequence[Category]:
        """Return all non-deleted categories ordered for display."""
        return self._repository.list()

    def get_category(self, category_id: UUIDv7) -> Category:
        """Return one category or raise when it does not exist."""
        category = self._repository.get(category_id)
        if category is None:
            raise CategoryNotFoundError
        return category

    def create_category(self, data: CategoryCreate) -> Category:
        """Create a category after validating slug and parent references."""
        self._ensure_slug_available(data.slug)
        self._ensure_parent_exists(data.parent_id)

        category = Category(
            title=data.title,
            slug=data.slug,
            parent_id=data.parent_id,
            sort_order=data.sort_order,
            is_active=data.is_active,
        )
        self._repository.add(category)
        self._session.commit()
        self._session.refresh(category)
        return category

    def update_category(self, category_id: UUIDv7, data: CategoryUpdate) -> Category:
        """Update a category after validating changed fields."""
        category = self.get_category(category_id)
        changes = data.model_dump(exclude_unset=True)

        if "slug" in changes:
            self._ensure_slug_available(data.slug, current_category_id=category_id)
            category.slug = data.slug

        if "parent_id" in changes:
            self._ensure_parent_exists(data.parent_id, current_category_id=category_id)
            category.parent_id = data.parent_id

        if data.title is not None:
            category.title = data.title
        if data.sort_order is not None:
            category.sort_order = data.sort_order
        if data.is_active is not None:
            category.is_active = data.is_active

        self._session.commit()
        self._session.refresh(category)
        return category

    def delete_category(self, category_id: UUIDv7) -> None:
        """Soft-delete a category while preserving catalog history."""
        category = self.get_category(category_id)
        category.soft_delete()
        self._session.commit()

    def _ensure_slug_available(
        self,
        slug: str | None,
        current_category_id: UUIDv7 | None = None,
    ) -> None:
        """Raise when a slug belongs to another active category."""
        if slug is None:
            return

        category = self._repository.get_by_slug(slug)
        if category is not None and category.id != current_category_id:
            raise CategorySlugAlreadyExistsError

    def _ensure_parent_exists(
        self,
        parent_id: UUIDv7 | None,
        current_category_id: UUIDv7 | None = None,
    ) -> None:
        """Raise when a parent category is missing or points to itself."""
        if parent_id is None:
            return
        if parent_id == current_category_id:
            raise CategoryParentError
        if self._repository.get(parent_id) is None:
            raise CategoryParentError
