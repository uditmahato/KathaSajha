"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .errors import CodedHTTPException
from .models import User
from .security import decode_access_token

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="auth.not_authenticated",
            detail="Not authenticated",
        )
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub") if payload else None
    if not user_id:
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="auth.token_invalid",
            detail="Invalid or expired token",
        )
    user = await db.get(User, user_id)
    if user is None:
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="auth.user_gone",
            detail="User no longer exists",
        )
    # A password change bumps token_version, retiring every token issued before
    # it. Tokens minted before this field existed carry no `ver` and are retired
    # too, which is the safe direction to fail.
    if payload.get("ver") != user.token_version:
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="auth.session_ended",
            detail="This session has ended. Please log in again.",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
