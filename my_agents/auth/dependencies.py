"""FastAPI dependencies for auth/session handling."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from my_agents.auth.abuse import AuthAbuseConfig, AuthAbuseProtector, get_auth_abuse_protector
from my_agents.auth.contracts import Principal
from my_agents.auth.email import AuthEmailSender, get_auth_email_sender
from my_agents.auth.service import AuthService, InvalidSessionError
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings


def get_auth_service(
    db: Annotated[Session, Depends(get_database_session)],
    email_sender: Annotated[AuthEmailSender, Depends(get_auth_email_sender)],
) -> AuthService:
    """Return an auth service bound to the request database session."""
    return AuthService(db, email_sender=email_sender)


def get_auth_abuse_guard(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthAbuseProtector:
    """Return the local auth abuse guard derived from runtime settings."""
    return get_auth_abuse_protector(
        AuthAbuseConfig(
            enabled=settings.auth_abuse_protection_enabled,
            max_attempts=settings.auth_abuse_max_attempts,
            window_seconds=settings.auth_abuse_window_seconds,
        )
    )


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
