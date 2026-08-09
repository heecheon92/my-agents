"""Stable machine-readable API error envelopes.

Human-readable ``detail`` remains backward compatible. Clients should branch on
``code`` and treat detail as diagnostic copy, not a localization key.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException


class APIErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    INVALID_CREDENTIALS = "invalid_credentials"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_NOT_FOUND = "resource_not_found"
    CONFLICT = "conflict"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    UPLOAD_TOO_LARGE = "upload_too_large"
    GUEST_ACCESS_EXPIRED = "guest_access_expired"
    GUEST_CONVERSATION_LIMIT_REACHED = "guest_conversation_limit_reached"
    GUEST_PROMPT_LIMIT_REACHED = "guest_prompt_limit_reached"
    GUEST_DOCUMENT_LIMIT_REACHED = "guest_document_limit_reached"
    PUBLISH_REQUEST_ALREADY_REVIEWED = "publish_request_already_reviewed"
    CONVERSATION_RUN_ALREADY_ACTIVE = "conversation_run_already_active"
    CONVERSATION_RUN_FAILED = "conversation_run_failed"
    REASONING_MODE_NOT_SUPPORTED = "reasoning_mode_not_supported"
    DOCUMENT_WORKSPACE_DISABLED = "document_workspace_disabled"
    GUEST_DOCUMENT_WORKSPACE_FORBIDDEN = "guest_document_workspace_forbidden"
    DOCUMENT_PROVIDER_CONSENT_REQUIRED = "document_provider_consent_required"
    UNSUPPORTED_ATTACHMENT_TYPE = "unsupported_attachment_type"
    ATTACHMENT_TOO_LARGE = "attachment_too_large"
    ATTACHMENT_UPLOAD_FAILED = "attachment_upload_failed"
    ATTACHMENT_DELETE_FAILED = "attachment_delete_failed"
    ATTACHMENT_LIMIT_EXCEEDED = "attachment_limit_exceeded"
    ATTACHMENT_NOT_FOUND = "attachment_not_found"
    ATTACHMENT_EXPIRED = "attachment_expired"
    ARTIFACT_NOT_FOUND = "artifact_not_found"
    ARTIFACT_EXPIRED = "artifact_expired"
    ARTIFACT_DOWNLOAD_FAILED = "artifact_download_failed"
    SERVICE_ERROR = "service_error"
    REQUEST_FAILED = "request_failed"


class APIErrorResponse(BaseModel):
    """Additive error envelope shared by HTTP and validation failures."""

    model_config = ConfigDict(extra="forbid")

    detail: str | list[dict[str, Any]]
    code: APIErrorCode


_STATUS_CODES: dict[int, APIErrorCode] = {
    400: APIErrorCode.INVALID_REQUEST,
    401: APIErrorCode.AUTHENTICATION_REQUIRED,
    403: APIErrorCode.PERMISSION_DENIED,
    404: APIErrorCode.RESOURCE_NOT_FOUND,
    409: APIErrorCode.CONFLICT,
    413: APIErrorCode.UPLOAD_TOO_LARGE,
    415: APIErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: APIErrorCode.INVALID_REQUEST,
    429: APIErrorCode.RATE_LIMIT_EXCEEDED,
    500: APIErrorCode.SERVICE_ERROR,
    502: APIErrorCode.SERVICE_ERROR,
    503: APIErrorCode.SERVICE_ERROR,
}


class APIHTTPException(HTTPException):
    """HTTP exception carrying a stable code independent from human prose."""

    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        code: APIErrorCode,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def install_api_error_handlers(app: FastAPI) -> None:
    """Install backward-compatible handlers that add a stable top-level code."""

    @app.exception_handler(StarletteHTTPException)
    async def coded_http_exception_handler(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        code = error_code_for_http_exception(exc)
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder({"detail": exc.detail, "code": code.value}),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def coded_validation_exception_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {"detail": exc.errors(), "code": APIErrorCode.INVALID_REQUEST.value}
            ),
        )


def error_code_for_http_exception(exc: StarletteHTTPException) -> APIErrorCode:
    """Resolve an explicit stable code, then fall back to a status category."""
    explicit_code = getattr(exc, "code", None)
    if isinstance(explicit_code, APIErrorCode):
        return explicit_code
    return _STATUS_CODES.get(exc.status_code, APIErrorCode.REQUEST_FAILED)


def document_upload_error_code(
    detail: str,
    *,
    unsupported_media_type: bool,
) -> APIErrorCode:
    """Classify the bounded document-upload error family at its API boundary."""
    if unsupported_media_type:
        return APIErrorCode.UNSUPPORTED_MEDIA_TYPE
    normalized = detail.casefold()
    if normalized.startswith("uploaded ") and (
        "size limit" in normalized or "exceeds the 5 mib" in normalized
    ):
        return APIErrorCode.UPLOAD_TOO_LARGE
    return APIErrorCode.INVALID_REQUEST
