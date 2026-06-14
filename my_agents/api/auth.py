"""First-party auth API routes."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.auth.abuse import AuthAbuseProtector, AuthRateLimitExceededError
from my_agents.auth.contracts import (
    Principal,
    UserType,
    can_manage_system_knowledge_for_user_type,
)
from my_agents.auth.dependencies import (
    get_auth_abuse_guard,
    get_auth_service,
    get_current_principal,
)
from my_agents.auth.email import AuthEmailLanguage, get_local_auth_email_outbox
from my_agents.auth.models import UserModel
from my_agents.auth.schemas import (
    AcceptedResponse,
    AccountNicknameUpdateRequest,
    AccountPasswordUpdateRequest,
    DevAuthEmailMessageResponse,
    GuestAccessRequest,
    GuestLoginRequest,
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
    AccountApprovalRequiredError,
    AccountMutationNotAllowedError,
    AccountRejectedError,
    AuthService,
    DuplicateEmailError,
    InvalidAuthTokenError,
    InvalidCredentialsError,
    InvalidCsrfTokenError,
    InvalidSessionError,
    UnverifiedEmailError,
)
from my_agents.diagnostics import deploy_log, safe_email_context
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
def signup(
    request: SignupRequest,
    http_request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    abuse_guard: Annotated[AuthAbuseProtector, Depends(get_auth_abuse_guard)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SignupResponse:
    """Create a user and send a local/dev email verification token."""
    email_context = _email_log_context(str(request.email))
    client_identifier = _request_client_identifier(http_request)
    deploy_log("auth.api.signup.received", client=client_identifier, **email_context)
    logger.info(
        "auth.signup.received email_hash=%s email_domain=%s client=%s",
        email_context["email_hash"],
        email_context["email_domain"],
        client_identifier,
    )
    if not settings.auth_signup_enabled:
        deploy_log(
            "auth.api.signup.rejected",
            reason="signup_disabled",
            client=client_identifier,
            **email_context,
        )
        logger.info(
            "auth.signup.rejected reason=signup_disabled email_hash=%s email_domain=%s client=%s",
            email_context["email_hash"],
            email_context["email_domain"],
            client_identifier,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="signup disabled",
        )
    email_identifier = _email_identifier(str(request.email))
    _assert_auth_allowed(abuse_guard, action="signup_email", identifier=email_identifier)
    _assert_auth_allowed(abuse_guard, action="signup_client", identifier=client_identifier)
    deploy_log("auth.api.signup.abuse_allowed", client=client_identifier, **email_context)
    abuse_guard.record_attempt(action="signup_email", identifier=email_identifier)
    abuse_guard.record_attempt(action="signup_client", identifier=client_identifier)
    deploy_log("auth.api.signup.abuse_recorded", client=client_identifier, **email_context)
    try:
        result = auth_service.signup(
            email=str(request.email),
            nickname=request.nickname,
            password=request.password,
            email_language=_auth_email_language(http_request),
            auto_approve=settings.account_signup_auto_approval,
        )
    except DuplicateEmailError as exc:
        deploy_log(
            "auth.api.signup.rejected",
            reason="email_unavailable",
            client=client_identifier,
            **email_context,
        )
        logger.info(
            "auth.signup.rejected reason=email_unavailable email_hash=%s email_domain=%s client=%s",
            email_context["email_hash"],
            email_context["email_domain"],
            client_identifier,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email unavailable",
        ) from exc
    except Exception as exc:
        deploy_log(
            "auth.api.signup.failed",
            client=client_identifier,
            error_class=exc.__class__.__name__,
            **email_context,
        )
        logger.error(
            "auth.signup.failed email_hash=%s email_domain=%s client=%s error_class=%s",
            email_context["email_hash"],
            email_context["email_domain"],
            client_identifier,
            exc.__class__.__name__,
        )
        raise
    deploy_log(
        "auth.api.signup.completed",
        user_id=result.user.id,
        verification_email_sent=result.verification_email_sent,
        **email_context,
    )
    logger.info(
        "auth.signup.completed user_id=%s email_hash=%s email_domain=%s verification_email_sent=%s",
        result.user.id,
        email_context["email_hash"],
        email_context["email_domain"],
        result.verification_email_sent,
    )
    return SignupResponse(
        user=_user_response(result.user),
        verification_email_sent=result.verification_email_sent,
        approval_required=result.user.approval_status == "pending",
    )


@auth_router.post("/guest/request", response_model=AcceptedResponse)
def request_guest_access_code(
    request: GuestAccessRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AcceptedResponse:
    """Record an email-gated guest access request without exposing a code publicly."""
    email_context = safe_email_context(str(request.email))
    deploy_log("auth.api.guest_request.received", **email_context)
    if not settings.guest_access_enabled:
        deploy_log(
            "auth.api.guest_request.rejected",
            reason="guest_access_disabled",
            **email_context,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guest access disabled")
    try:
        if settings.guest_code_auto_approval:
            auth_service.issue_and_send_guest_access_code(
                email=str(request.email),
                ttl=timedelta(seconds=settings.guest_code_ttl_seconds),
                email_language=request.language,
            )
        else:
            auth_service.request_guest_access(email=str(request.email))
    except Exception as exc:
        deploy_log(
            "auth.api.guest_request.failed",
            error_class=exc.__class__.__name__,
            **email_context,
        )
        if settings.guest_code_auto_approval:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="guest access temporarily unavailable",
            ) from exc
        raise
    deploy_log("auth.api.guest_request.completed", **email_context)
    return AcceptedResponse()


@auth_router.post("/guest/login", response_model=LoginResponse)
def guest_login(
    request: GuestLoginRequest,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """Redeem a one-time guest code and issue the normal app session cookie."""
    if not settings.guest_access_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guest access disabled")
    try:
        authenticated = auth_service.redeem_guest_access_code(
            code=request.code,
            access_ttl=timedelta(seconds=settings.guest_access_ttl_seconds),
        )
    except InvalidAuthTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired guest code",
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
    except AccountApprovalRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account approval pending",
        ) from exc
    except AccountRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account unavailable",
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
    auth_service.request_password_reset(
        email=str(request.email),
        email_language=_auth_email_language(http_request),
    )
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


@auth_router.patch("/me/nickname", response_model=UserResponse)
def update_nickname(
    request: AccountNicknameUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    """Update the current registered user's display nickname."""
    try:
        user = auth_service.update_nickname(
            user_id=principal.user_id,
            current_password=request.current_password,
            nickname=request.nickname,
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid current password",
        ) from exc
    except AccountMutationNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="registered account required",
        ) from exc
    except InvalidSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        ) from exc
    return _user_response(user)


@auth_router.patch("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def update_password(
    request: AccountPasswordUpdateRequest,
    response: Response,
    principal: Annotated[Principal, Depends(get_current_principal)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Update the current registered user's password and clear the browser session."""
    try:
        auth_service.update_password(
            user_id=principal.user_id,
            current_password=request.current_password,
            new_password=request.new_password,
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid current password",
        ) from exc
    except AccountMutationNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="registered account required",
        ) from exc
    except InvalidSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        ) from exc
    response.delete_cookie(key=settings.session_cookie_name)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@auth_router.get("/dev/outbox", response_model=list[DevAuthEmailMessageResponse])
def dev_auth_outbox(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> list[DevAuthEmailMessageResponse]:
    """Return local auth emails only when explicitly enabled for local demos.

    This endpoint exists so a separate frontend can complete signup -> verify-email in
    deterministic local runs. It is disabled by default and should never be enabled for
    public deployments.
    """
    if not settings.auth_dev_outbox_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    session_token = request.cookies.get(settings.session_cookie_name)
    if session_token is not None:
        try:
            principal = auth_service.authenticate_session(session_token)
        except InvalidSessionError:
            principal = None
        if principal is not None and principal.is_guest:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")
    return [
        DevAuthEmailMessageResponse(
            recipient_email=message.recipient_email,
            purpose=message.purpose,
            subject=message.subject,
            body=message.body,
            token=message.token,
        )
        for message in get_local_auth_email_outbox().messages()
    ]


def _user_response(user: UserModel) -> UserResponse:
    is_guest = user.account_type == "guest"
    user_type = UserType(user.user_type or UserType.NORMAL.value)
    return UserResponse(
        id=user.id,
        email=None if is_guest else user.email,
        nickname=user.nickname,
        email_verified_at=user.email_verified_at,
        approval_status="approved" if is_guest else user.approval_status,
        is_guest=is_guest,
        guest_expires_at=user.guest_expires_at if is_guest else None,
        user_type=user_type,
        can_manage_system_knowledge=(
            False if is_guest else can_manage_system_knowledge_for_user_type(user_type.value)
        ),
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


def _email_log_context(email: str) -> dict[str, str]:
    normalized = _email_identifier(email)
    _, _, domain = normalized.partition("@")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return {"email_hash": digest, "email_domain": domain or "unknown"}


def _request_client_identifier(request: Request) -> str:
    if request.client is None:
        return "client:unknown"
    return f"client:{request.client.host}"


def _auth_email_language(request: Request) -> AuthEmailLanguage:
    explicit_locale = request.headers.get("x-my-agents-language") or request.headers.get(
        "x-my-agents-locale"
    )
    explicit_language = _supported_auth_email_language(explicit_locale)
    if explicit_language is not None:
        return explicit_language
    accept_language = request.headers.get("accept-language")
    for item in (accept_language or "").split(","):
        language = _supported_auth_email_language(item.split(";", 1)[0])
        if language is not None:
            return language
    return "ko"


def _supported_auth_email_language(value: str | None) -> AuthEmailLanguage | None:
    language = (value or "").strip().casefold()
    if language.startswith("ko"):
        return "ko"
    if language.startswith("en"):
        return "en"
    return None


def _email_identifier(email: str) -> str:
    return f"email:{email.strip().casefold()}"
