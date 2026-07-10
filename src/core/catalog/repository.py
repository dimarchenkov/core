from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.catalog.models import Category
from core.shared.db import UUIDv7


class CategoryRepository:
    """Database access for catalog categories."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to a database session."""
        self._session = session

    def add(self, category: Category) -> Category:
        """Add a category to the current unit of work."""
        self._session.add(category)
        return category

    def get(self, category_id: UUIDv7) -> Category | None:
        """Return an active category by id, excluding soft-deleted rows."""
        statement = select(Category).where(
            Category.id == category_id,
            Category.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def get_by_slug(self, slug: str) -> Category | None:
        """Return an active category by slug, excluding soft-deleted rows."""
        statement = select(Category).where(
            Category.slug == slug,
            Category.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def list(self) -> Sequence[Category]:
        """Return categories ordered for catalog navigation."""
        statement = (
            select(Category)
            .where(Category.deleted_at.is_(None))
            .order_by(Category.sort_order, Category.title)
        )
        return self._session.scalars(statement).all()
