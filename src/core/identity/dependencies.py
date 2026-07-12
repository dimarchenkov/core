from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from core.config import get_settings
from core.database import get_session
from core.identity.models import User
from core.identity.repository import UserRepository
from core.identity.security import AuthenticationTokenError, decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """Return the active user identified by a valid bearer access token."""
    settings = get_settings()
    try:
        user_id = decode_access_token(token, settings.jwt_secret, settings.jwt_algorithm)
    except AuthenticationTokenError as exc:
        raise _credentials_exception() from exc
    user = UserRepository(session).get_active_by_id(user_id)
    if user is None:
        raise _credentials_exception()
    return user


def _credentials_exception() -> HTTPException:
    """Build the standard bearer authentication failure response."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
