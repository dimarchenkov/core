from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from core.config import get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.identity.schemas import AccessToken, UserRead
from core.identity.security import create_access_token
from core.identity.service import IdentityService, InvalidCredentialsError

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/login", response_model=AccessToken, status_code=status.HTTP_200_OK)
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Annotated[Session, Depends(get_session)],
) -> AccessToken:
    """Authenticate an email/password pair and return a bearer access token."""
    try:
        user = IdentityService(session).authenticate_user(form_data.username, form_data.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    settings = get_settings()
    return AccessToken(
        access_token=create_access_token(
            user.id,
            settings.jwt_secret,
            settings.jwt_algorithm,
            settings.jwt_access_token_expire_minutes,
        ),
        token_type="bearer",
    )


@router.get("/me", response_model=UserRead)
def read_current_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Return the public representation of the current authenticated user."""
    return user
