"""Pydantic schemas for first-party auth endpoints."""

from __future__ import annotations

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


class UserResponse(BaseModel):
    """Safe user payload. Never include password hashes or session tokens."""

    model_config = ConfigDict(extra="forbid")

    id: str
    email: EmailStr


class LoginResponse(BaseModel):
    """Login response with safe user data and CSRF proof for mutating requests."""

    model_config = ConfigDict(extra="forbid")

    user: UserResponse
    csrf_token: str = Field(min_length=1)
