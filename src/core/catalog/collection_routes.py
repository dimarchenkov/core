from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.catalog.collection_schemas import (
    CollectionCreate,
    CollectionDetailRead,
    CollectionMembershipAdd,
    CollectionPreviewRead,
    CollectionRulesUpdate,
    CollectionSummaryRead,
    CollectionUpdate,
    CollectionVariantRead,
    VariantCollectionsRead,
    VariantStaticCollectionsUpdate,
)
from core.catalog.collection_service import (
    CollectionMembershipError,
    CollectionNotFoundError,
    CollectionRuleValidationError,
    CollectionService,
    CollectionSlugConflictError,
    CollectionTypeChangeError,
    CollectionVariantNotFoundError,
)
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/catalog/collections",
    tags=["catalog"],
    dependencies=[Depends(get_current_user)],
)


def get_collection_service(
    session: Annotated[Session, Depends(get_session)],
) -> CollectionService:
    """Provide Collection workflows bound to the request transaction."""
    return CollectionService(session)


def _actor_id(current_user: User | None) -> UUIDv7 | None:
    """Return the authenticated actor while preserving route test overrides."""
    return current_user.id if current_user is not None else None


def _translate_error(exc: Exception) -> HTTPException:
    """Map stable Collection application errors to operator-facing HTTP errors."""
    if isinstance(exc, CollectionNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found.")
    if isinstance(exc, CollectionVariantNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found.")
    if isinstance(exc, (CollectionSlugConflictError, CollectionMembershipError)):
        detail = str(exc) or "Collection conflict."
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    if isinstance(exc, (CollectionRuleValidationError, CollectionTypeChangeError)):
        detail = str(exc) or "Collection type cannot be changed."
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    raise exc


@router.get("", response_model=list[CollectionSummaryRead])
def list_collections(
    service: Annotated[CollectionService, Depends(get_collection_service)],
    include_inactive: Annotated[bool, Query()] = False,
) -> list[CollectionSummaryRead]:
    """List normal active Collections or the full management set."""
    return service.list_collections(include_inactive=include_inactive)


@router.post("", response_model=CollectionDetailRead, status_code=status.HTTP_201_CREATED)
def create_collection(
    data: CollectionCreate,
    service: Annotated[CollectionService, Depends(get_collection_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CollectionDetailRead:
    """Create one STATIC or DYNAMIC Collection."""
    try:
        result = service.create_collection(data, actor_id=_actor_id(current_user))
        session.commit()
        return result
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Collection slug already exists.",
        ) from exc
    except (
        CollectionSlugConflictError,
        CollectionRuleValidationError,
    ) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    except Exception:
        session.rollback()
        raise


@router.post("/preview", response_model=CollectionPreviewRead)
def preview_collection_rules(
    data: CollectionRulesUpdate,
    service: Annotated[CollectionService, Depends(get_collection_service)],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> CollectionPreviewRead:
    """Preview unsaved DYNAMIC rules against current Catalog facts."""
    try:
        return service.preview_rules(data.rules, limit=limit)
    except CollectionRuleValidationError as exc:
        raise _translate_error(exc) from exc


@router.get("/variant-search", response_model=list[CollectionVariantRead])
def search_collection_variants(
    service: Annotated[CollectionService, Depends(get_collection_service)],
    query: Annotated[str, Query(min_length=1, max_length=255)],
    limit: Annotated[int, Query(ge=1, le=20)] = 12,
) -> list[CollectionVariantRead]:
    """Search operational Variants for a STATIC Collection."""
    return service.search_variants(query, limit=limit)


@router.get("/variants/{variant_id}", response_model=VariantCollectionsRead)
def get_variant_collections(
    variant_id: UUIDv7,
    service: Annotated[CollectionService, Depends(get_collection_service)],
) -> VariantCollectionsRead:
    """Return editable static memberships and computed dynamic matches."""
    try:
        return service.get_variant_collections(variant_id)
    except CollectionVariantNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.put("/variants/{variant_id}", response_model=VariantCollectionsRead)
def replace_variant_static_collections(
    variant_id: UUIDv7,
    data: VariantStaticCollectionsUpdate,
    service: Annotated[CollectionService, Depends(get_collection_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> VariantCollectionsRead:
    """Replace one Variant's active STATIC Collection memberships."""
    try:
        result = service.replace_variant_static_collections(
            variant_id,
            data.collection_ids,
            actor_id=_actor_id(current_user),
        )
        session.commit()
        return result
    except (CollectionVariantNotFoundError, CollectionMembershipError) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Variant already belongs to this Collection.",
        ) from exc
    except Exception:
        session.rollback()
        raise


@router.get("/{collection_id}", response_model=CollectionDetailRead)
def get_collection(
    collection_id: UUIDv7,
    service: Annotated[CollectionService, Depends(get_collection_service)],
) -> CollectionDetailRead:
    """Return one Collection with live count and typed rules."""
    try:
        return service.get_collection(collection_id)
    except CollectionNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.patch("/{collection_id}", response_model=CollectionDetailRead)
def update_collection(
    collection_id: UUIDv7,
    data: CollectionUpdate,
    service: Annotated[CollectionService, Depends(get_collection_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CollectionDetailRead:
    """Change mutable metadata or enable/disable one Collection."""
    try:
        result = service.update_collection(
            collection_id,
            data,
            actor_id=_actor_id(current_user),
        )
        session.commit()
        return result
    except (
        CollectionNotFoundError,
        CollectionSlugConflictError,
        CollectionTypeChangeError,
    ) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Collection slug already exists.",
        ) from exc
    except Exception:
        session.rollback()
        raise


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_collection(
    collection_id: UUIDv7,
    service: Annotated[CollectionService, Depends(get_collection_service)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Delete one Collection and its owned links, never its Variants."""
    try:
        service.delete_collection(collection_id)
        session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except CollectionNotFoundError as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    except Exception:
        session.rollback()
        raise


@router.put("/{collection_id}/rules", response_model=CollectionDetailRead)
def replace_collection_rules(
    collection_id: UUIDv7,
    data: CollectionRulesUpdate,
    service: Annotated[CollectionService, Depends(get_collection_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CollectionDetailRead:
    """Replace the complete AND-rule set of a DYNAMIC Collection."""
    try:
        result = service.replace_rules(
            collection_id,
            data.rules,
            actor_id=_actor_id(current_user),
        )
        session.commit()
        return result
    except (CollectionNotFoundError, CollectionRuleValidationError) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    except Exception:
        session.rollback()
        raise


@router.get("/{collection_id}/variants", response_model=list[CollectionVariantRead])
def list_collection_variants(
    collection_id: UUIDv7,
    service: Annotated[CollectionService, Depends(get_collection_service)],
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> list[CollectionVariantRead]:
    """Resolve Collection membership through the shared query boundary."""
    try:
        return service.get_collection_variants(collection_id, limit=limit)
    except CollectionNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.post("/{collection_id}/memberships", response_model=CollectionDetailRead)
def add_collection_members(
    collection_id: UUIDv7,
    data: CollectionMembershipAdd,
    service: Annotated[CollectionService, Depends(get_collection_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CollectionDetailRead:
    """Add concrete Variants to a STATIC Collection."""
    try:
        result = service.add_members(
            collection_id,
            data.variant_ids,
            actor_id=_actor_id(current_user),
        )
        session.commit()
        return result
    except (
        CollectionNotFoundError,
        CollectionVariantNotFoundError,
        CollectionMembershipError,
    ) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Variant is already a member.",
        ) from exc
    except Exception:
        session.rollback()
        raise


@router.delete("/{collection_id}/memberships/{variant_id}", response_model=CollectionDetailRead)
def remove_collection_member(
    collection_id: UUIDv7,
    variant_id: UUIDv7,
    service: Annotated[CollectionService, Depends(get_collection_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CollectionDetailRead:
    """Remove one STATIC association without deleting its Variant."""
    try:
        result = service.remove_member(
            collection_id,
            variant_id,
            actor_id=_actor_id(current_user),
        )
        session.commit()
        return result
    except (CollectionNotFoundError, CollectionMembershipError) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    except Exception:
        session.rollback()
        raise
