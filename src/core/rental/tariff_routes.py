from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.rental.tariff_schemas import RentalTariffCreate, RentalTariffRead, RentalTariffUpdate
from core.rental.tariff_service import (
    RentableVariantNotFoundError,
    RentalTariffDeleteBlockedError,
    RentalTariffNotFoundError,
    RentalTariffService,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/rental",
    tags=["rental tariffs"],
    dependencies=[Depends(get_current_user)],
)


def get_tariff_service(
    session: Annotated[Session, Depends(get_session)],
) -> RentalTariffService:
    """Provide tariff application services for one request transaction."""
    return RentalTariffService(session)


def _read(record: object) -> RentalTariffRead:
    return RentalTariffRead.from_record(record)


@router.get("/variants/{variant_id}/tariffs", response_model=list[RentalTariffRead])
def list_tariffs(
    variant_id: UUIDv7,
    service: Annotated[RentalTariffService, Depends(get_tariff_service)],
    view: Literal["selectable", "management"] = "selectable",
    on_date: Annotated[date | None, Query()] = None,
) -> list[RentalTariffRead]:
    """Return canonical selectable tariffs or the operator's active schedule."""
    try:
        records = (
            service.list_management(variant_id)
            if view == "management"
            else service.list_selectable(variant_id, on_date=on_date)
        )
        return [_read(record) for record in records]
    except RentableVariantNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Catalog variant not found.") from exc


@router.post(
    "/variants/{variant_id}/tariffs",
    response_model=RentalTariffRead,
    status_code=status.HTTP_201_CREATED,
)
def create_tariff(
    variant_id: UUIDv7,
    data: RentalTariffCreate,
    service: Annotated[RentalTariffService, Depends(get_tariff_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalTariffRead:
    """Create a tariff for an operationally rentable CatalogVariant."""
    try:
        tariff = service.create(variant_id, data, actor_id=current_user.id)
        session.commit()
        session.refresh(tariff)
        return _read(tariff)
    except RentableVariantNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Rentable catalog variant not found.") from exc
    except Exception:
        session.rollback()
        raise


@router.put("/tariffs/{tariff_id}", response_model=RentalTariffRead)
def create_tariff_version(
    tariff_id: UUIDv7,
    data: RentalTariffUpdate,
    service: Annotated[RentalTariffService, Depends(get_tariff_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalTariffRead:
    """Create a new version instead of overwriting meaningful terms."""
    try:
        tariff = service.create_version(tariff_id, data, actor_id=current_user.id)
        session.commit()
        session.refresh(tariff)
        return _read(tariff)
    except (RentalTariffNotFoundError, RentableVariantNotFoundError) as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Rental tariff not found.") from exc
    except Exception:
        session.rollback()
        raise


@router.get("/tariffs/{tariff_id}/history", response_model=list[RentalTariffRead])
def tariff_history(
    tariff_id: UUIDv7,
    service: Annotated[RentalTariffService, Depends(get_tariff_service)],
) -> list[RentalTariffRead]:
    """Return every preserved version in one tariff lineage."""
    try:
        return [_read(record) for record in service.history(tariff_id)]
    except RentalTariffNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental tariff not found.") from exc


@router.post("/tariffs/{tariff_id}/archive", response_model=RentalTariffRead)
def archive_tariff(
    tariff_id: UUIDv7,
    service: Annotated[RentalTariffService, Depends(get_tariff_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalTariffRead:
    """Archive a selectable version while preserving its commercial history."""
    try:
        tariff = service.archive(tariff_id, actor_id=current_user.id)
        session.commit()
        session.refresh(tariff)
        return _read(tariff)
    except RentalTariffNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Rental tariff not found.") from exc


@router.delete("/tariffs/{tariff_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_unused_tariff(
    tariff_id: UUIDv7,
    service: Annotated[RentalTariffService, Depends(get_tariff_service)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Hard-delete only a never-used tariff with no version history."""
    try:
        service.hard_delete_unused(tariff_id)
        session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except RentalTariffNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Rental tariff not found.") from exc
    except RentalTariffDeleteBlockedError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Used or versioned rental tariffs cannot be hard-deleted.",
        ) from exc
