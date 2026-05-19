"""First-party auth API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.auth.abuse import AuthAbuseProtector, AuthRateLimitExceededError
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import (
    get_auth_abuse_guard,
    get_auth_service,
    get_current_principal,
)
from my_agents.auth.models import UserModel
from my_agents.auth.schemas import (
    AcceptedResponse,
    LoginRequest,
    LoginResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    SignupRequest,
    SignupResponse,
    UserResponse,
    VerifyEmailRequest,
)
from my_agents.auth.service import (
    AuthService,
    DuplicateEmailError,
    InvalidAuthTokenError,
    InvalidCredentialsError,
    InvalidCsrfTokenError,
    InvalidSessionError,
    UnverifiedEmailError,
)
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
def signup(
    request: SignupRequest,
    http_request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    abuse_guard: Annotated[AuthAbuseProtector, Depends(get_auth_abuse_guard)],
) -> SignupResponse:
    """Create a user and send a local/dev email verification token."""
    email_identifier = _email_identifier(str(request.email))
    client_identifier = _request_client_identifier(http_request)
    _assert_auth_allowed(abuse_guard, action="signup_email", identifier=email_identifier)
    _assert_auth_allowed(abuse_guard, action="signup_client", identifier=client_identifier)
    abuse_guard.record_attempt(action="signup_email", identifier=email_identifier)
    abuse_guard.record_attempt(action="signup_client", identifier=client_identifier)
    try:
        result = auth_service.signup(email=str(request.email), password=request.password)
    except DuplicateEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email unavailable",
        ) from exc
    return SignupResponse(
        user=_user_response(result.user),
        verification_email_sent=result.verification_email_sent,
    )


@auth_router.post("/verify-email", response_model=UserResponse)
def verify_email(
    request: VerifyEmailRequest,
    http_request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    abuse_guard: Annotated[AuthAbuseProtector, Depends(get_auth_abuse_guard)],
) -> UserResponse:
    """Consume an email verification token and mark the user verified."""
    client_identifier = _request_client_identifier(http_request)
    action = "verify_email_token"
    _assert_auth_allowed(abuse_guard, action=action, identifier=client_identifier)
    try:
        user = auth_service.verify_email(token=request.token)
    except InvalidAuthTokenError as exc:
        abuse_guard.record_attempt(action=action, identifier=client_identifier)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired token",
        ) from exc
    abuse_guard.reset(action=action, identifier=client_identifier)
    return _user_response(user)


@auth_router.post("/login", response_model=LoginResponse)
def login(
    request: LoginRequest,
    http_request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    abuse_guard: Annotated[AuthAbuseProtector, Depends(get_auth_abuse_guard)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """Authenticate verified credentials and issue an app-owned opaque session cookie."""
    email_identifier = _email_identifier(str(request.email))
    client_identifier = _request_client_identifier(http_request)
    _assert_auth_allowed(abuse_guard, action="login_email", identifier=email_identifier)
    _assert_auth_allowed(abuse_guard, action="login_client", identifier=client_identifier)
    try:
        authenticated = auth_service.login(email=str(request.email), password=request.password)
    except InvalidCredentialsError as exc:
        abuse_guard.record_attempt(action="login_email", identifier=email_identifier)
        abuse_guard.record_attempt(action="login_client", identifier=client_identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or password",
        ) from exc
    except UnverifiedEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="email verification required",
        ) from exc
    abuse_guard.reset(action="login_email", identifier=email_identifier)
    abuse_guard.reset(action="login_client", identifier=client_identifier)
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


@auth_router.post(
    "/password-reset/request",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_password_reset(
    request: PasswordResetRequest,
    http_request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    abuse_guard: Annotated[AuthAbuseProtector, Depends(get_auth_abuse_guard)],
) -> AcceptedResponse:
    """Request a reset token without revealing whether the account exists."""
    email_identifier = _email_identifier(str(request.email))
    client_identifier = _request_client_identifier(http_request)
    _assert_auth_allowed(abuse_guard, action="password_reset_email", identifier=email_identifier)
    _assert_auth_allowed(abuse_guard, action="password_reset_client", identifier=client_identifier)
    abuse_guard.record_attempt(action="password_reset_email", identifier=email_identifier)
    abuse_guard.record_attempt(action="password_reset_client", identifier=client_identifier)
    auth_service.request_password_reset(email=str(request.email))
    return AcceptedResponse()


@auth_router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(
    request: PasswordResetConfirmRequest,
    http_request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    abuse_guard: Annotated[AuthAbuseProtector, Depends(get_auth_abuse_guard)],
) -> Response:
    """Consume a reset token, replace the password, and revoke existing sessions."""
    client_identifier = _request_client_identifier(http_request)
    action = "password_reset_token"
    _assert_auth_allowed(abuse_guard, action=action, identifier=client_identifier)
    try:
        auth_service.confirm_password_reset(token=request.token, new_password=request.new_password)
    except InvalidAuthTokenError as exc:
        abuse_guard.record_attempt(action=action, identifier=client_identifier)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired token",
        ) from exc
    abuse_guard.reset(action=action, identifier=client_identifier)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    return UserResponse(
        id=user.id,
        email=user.email,
        email_verified_at=user.email_verified_at,
    )


def _assert_auth_allowed(
    abuse_guard: AuthAbuseProtector,
    *,
    action: str,
    identifier: str,
) -> None:
    try:
        abuse_guard.assert_allowed(action=action, identifier=identifier)
    except AuthRateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many auth attempts",
        ) from exc


def _request_client_identifier(request: Request) -> str:
    if request.client is None:
        return "client:unknown"
    return f"client:{request.client.host}"


def _email_identifier(email: str) -> str:
    return f"email:{email.strip().casefold()}"
