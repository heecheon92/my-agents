"""FastAPI dependencies for auth/session handling."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.service import AuthService, InvalidSessionError
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings


def get_auth_service(
    db: Annotated[Session, Depends(get_database_session)],
) -> AuthService:
    """Return an auth service bound to the request database session."""
    return AuthService(db)


def get_current_principal(
    request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    """Authenticate the current cookie session and return a safe principal."""
    session_token = request.cookies.get(settings.session_cookie_name)
    try:
        return auth_service.authenticate_session(session_token)
    except InvalidSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        ) from exc
