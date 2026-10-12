from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_admin, get_current_user
from core.identity.models import User
from core.rental.agreement_renderer import (
    AgreementDocumentTooLargeError,
    AgreementTemplateValidationError,
)
from core.rental.agreement_schemas import (
    AgreementPreviewRead,
    AgreementSettingsContext,
    AgreementTemplateCreate,
    AgreementTemplatePreviewRequest,
    AgreementTemplateVersionRead,
    RentalAgreementRead,
    RentalAgreementSettingsRead,
    RentalAgreementSettingsWrite,
)
from core.rental.agreement_service import (
    RentalAgreementNotFoundError,
    RentalAgreementOrderInvalidError,
    RentalAgreementService,
    RentalAgreementSettingsIncompleteError,
    RentalAgreementTemplateService,
)
from core.shared.db import UUIDv7

settings_router = APIRouter(
    prefix="/api/settings/rental/agreement",
    tags=["settings", "rental-agreements"],
    dependencies=[Depends(get_current_admin)],
)
agreement_router = APIRouter(
    prefix="/rental",
    tags=["rental-agreements"],
    dependencies=[Depends(get_current_user)],
)


def _template_service(
    session: Annotated[Session, Depends(get_session)],
) -> RentalAgreementTemplateService:
    return RentalAgreementTemplateService(session)


def _agreement_service(session: Annotated[Session, Depends(get_session)]) -> RentalAgreementService:
    return RentalAgreementService(session)


@settings_router.get("", response_model=AgreementSettingsContext)
def agreement_settings_context(
    service: Annotated[RentalAgreementTemplateService, Depends(_template_service)],
) -> AgreementSettingsContext:
    """Return lessor settings, current Markdown, placeholders, and version history."""
    return service.context()


@settings_router.put("/lessor", response_model=RentalAgreementSettingsRead)
def update_agreement_settings(
    data: RentalAgreementSettingsWrite,
    service: Annotated[RentalAgreementTemplateService, Depends(_template_service)],
    current_user: Annotated[User, Depends(get_current_admin)],
) -> RentalAgreementSettingsRead:
    """Update lessor facts for future agreements only."""
    return service.update_settings(data, actor_id=current_user.id)


@settings_router.post(
    "/templates", response_model=AgreementTemplateVersionRead, status_code=status.HTTP_201_CREATED
)
def create_agreement_template(
    data: AgreementTemplateCreate,
    service: Annotated[RentalAgreementTemplateService, Depends(_template_service)],
    current_user: Annotated[User, Depends(get_current_admin)],
) -> AgreementTemplateVersionRead:
    """Append an immutable agreement template version."""
    try:
        return service.create_template(data, actor_id=current_user.id)
    except AgreementTemplateValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@settings_router.post("/preview", response_model=AgreementPreviewRead)
def preview_agreement_template(
    data: AgreementTemplatePreviewRequest,
    service: Annotated[RentalAgreementTemplateService, Depends(_template_service)],
) -> AgreementPreviewRead:
    """Preview Markdown without saving a version."""
    try:
        return service.preview_template(data.markdown_source)
    except AgreementTemplateValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@agreement_router.get("/orders/{order_id}/agreement", response_model=RentalAgreementRead | None)
def get_order_agreement(
    order_id: UUIDv7,
    service: Annotated[RentalAgreementService, Depends(_agreement_service)],
) -> RentalAgreementRead | None:
    """Return the order's generated agreement without live re-rendering."""
    return service.get_by_order(order_id)


@agreement_router.get("/orders/{order_id}/agreement-preview", response_model=AgreementPreviewRead)
def preview_order_agreement(
    order_id: UUIDv7,
    service: Annotated[RentalAgreementService, Depends(_agreement_service)],
) -> AgreementPreviewRead:
    """Render a transient whole-order agreement preview."""
    try:
        return service.preview(order_id)
    except RentalAgreementOrderInvalidError as exc:
        raise HTTPException(status_code=409, detail="Заказ должен быть готов к выдаче.") from exc
    except AgreementDocumentTooLargeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@agreement_router.post(
    "/orders/{order_id}/agreements",
    response_model=RentalAgreementRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_order_agreement(
    order_id: UUIDv7,
    service: Annotated[RentalAgreementService, Depends(_agreement_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalAgreementRead:
    """Freeze one immutable agreement and canonical PDF without issuing equipment."""
    try:
        return service.generate(order_id, actor_id=current_user.id)
    except RentalAgreementOrderInvalidError as exc:
        raise HTTPException(status_code=409, detail="Заказ должен быть готов к выдаче.") from exc
    except RentalAgreementSettingsIncompleteError as exc:
        raise HTTPException(
            status_code=409,
            detail="Заполните реквизиты арендодателя в Настройки → Аренда → Договор.",
        ) from exc
    except AgreementDocumentTooLargeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@agreement_router.get("/agreements/{agreement_id}", response_model=RentalAgreementRead)
def get_agreement(
    agreement_id: UUIDv7,
    service: Annotated[RentalAgreementService, Depends(_agreement_service)],
) -> RentalAgreementRead:
    """Return immutable agreement metadata."""
    try:
        return service.get(agreement_id)
    except RentalAgreementNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Договор не найден.") from exc


@agreement_router.get("/agreements/{agreement_id}/pdf")
def get_agreement_pdf(
    agreement_id: UUIDv7,
    service: Annotated[RentalAgreementService, Depends(_agreement_service)],
) -> Response:
    """Return exact originally generated PDF bytes for open, print, or reprint."""
    try:
        number, content, digest = service.pdf(agreement_id)
    except RentalAgreementNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Договор не найден.") from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{number}.pdf"',
            "X-Document-SHA256": digest,
        },
    )
