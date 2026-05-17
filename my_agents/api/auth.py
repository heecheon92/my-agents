"""First-party auth API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_auth_service, get_current_principal
from my_agents.auth.models import UserModel
from my_agents.auth.schemas import LoginRequest, LoginResponse, SignupRequest, UserResponse
from my_agents.auth.service import (
    AuthService,
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidCsrfTokenError,
    InvalidSessionError,
)
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(
    request: SignupRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    """Create a first-party user account without returning password material."""
    try:
        user = auth_service.signup(email=str(request.email), password=request.password)
    except DuplicateEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email unavailable",
        ) from exc
    return _user_response(user)


@auth_router.post("/login", response_model=LoginResponse)
def login(
    request: LoginRequest,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """Authenticate credentials and issue an app-owned opaque session cookie."""
    try:
        authenticated = auth_service.login(email=str(request.email), password=request.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or password",
        ) from exc
    response.set_cookie(
        key=settings.session_cookie_name,
        value=authenticated.session_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    return LoginResponse(
        user=_user_response(authenticated.user),
        csrf_token=authenticated.csrf_token,
    )


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Revoke the current session.

    Browser clients authenticate with the configured HttpOnly cookie and prove mutating
    intent with the configured CSRF header.
    """
    session_token = request.cookies.get(settings.session_cookie_name)
    csrf_token = request.headers.get(settings.csrf_header_name)
    try:
        auth_service.logout(session_token=session_token, csrf_token=csrf_token)
    except InvalidCsrfTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid CSRF token",
        ) from exc
    except InvalidSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        ) from exc
    response.delete_cookie(key=settings.session_cookie_name)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@auth_router.get("/me", response_model=UserResponse)
def me(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> UserResponse:
    """Return the current authenticated user."""
    user = db.scalar(select(UserModel).where(UserModel.id == principal.user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return _user_response(user)


def _user_response(user: UserModel) -> UserResponse:
    return UserResponse(id=user.id, email=user.email)
