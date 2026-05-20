"""Pydantic schemas for first-party auth endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SignupRequest(BaseModel):
    """Input payload for first-party signup."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("password must not be blank")
        return value


class LoginRequest(BaseModel):
    """Input payload for first-party login."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyEmailRequest(BaseModel):
    """Input payload for email verification."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)


class PasswordResetRequest(BaseModel):
    """Input payload for requesting a password reset email."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    """Input payload for consuming a password reset token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def new_password_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("new_password must not be blank")
        return value


class UserResponse(BaseModel):
    """Safe user payload. Never include password hashes or session tokens."""

    model_config = ConfigDict(extra="forbid")

    id: str
    email: EmailStr
    email_verified_at: datetime | None


class SignupResponse(BaseModel):
    """Signup response with safe user data and delivery status."""

    model_config = ConfigDict(extra="forbid")

    user: UserResponse
    verification_email_sent: bool


class LoginResponse(BaseModel):
    """Login response with safe user data and CSRF proof for mutating requests."""

    model_config = ConfigDict(extra="forbid")

    user: UserResponse
    csrf_token: str = Field(min_length=1)


class AcceptedResponse(BaseModel):
    """Generic accepted response for non-enumerating auth requests."""

    model_config = ConfigDict(extra="forbid")

    status: str = "accepted"


class DevAuthEmailMessageResponse(BaseModel):
    """Development-only local auth email payload for deterministic demos."""

    model_config = ConfigDict(extra="forbid")

    recipient_email: EmailStr
    purpose: str
    subject: str
    body: str
    token: str
