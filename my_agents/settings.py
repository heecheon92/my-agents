"""Runtime configuration for local and OpenAI-backed assistant behavior."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ResponseMode = Literal["deterministic", "openai"]
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]
TextVerbosity = Literal["low", "medium", "high"]
SameSitePolicy = Literal["lax", "strict", "none"]
DeploymentEnvironment = Literal["local", "preview", "production"]
AuthEmailMode = Literal["local", "smtp"]


class Settings(BaseSettings):
    """Application settings loaded from process env and optional local `.env`.

    Real API keys should use the standard `OPENAI_API_KEY` environment variable.
    Project-specific knobs are namespaced with `MY_AGENTS_`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    response_mode: ResponseMode = Field(
        default="openai",
        validation_alias=AliasChoices("MY_AGENTS_RESPONSE_MODE"),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "MY_AGENTS_OPENAI_API_KEY"),
    )
    openai_model: str = Field(
        default="gpt-5.5",
        min_length=1,
        validation_alias=AliasChoices("MY_AGENTS_OPENAI_MODEL"),
    )
    openai_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=120,
        validation_alias=AliasChoices("MY_AGENTS_OPENAI_TIMEOUT_SECONDS"),
    )
    openai_max_output_tokens: int = Field(
        default=1200,
        ge=16,
        le=4096,
        validation_alias=AliasChoices("MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS"),
    )
    openai_reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        validation_alias=AliasChoices("MY_AGENTS_OPENAI_REASONING_EFFORT"),
    )
    openai_verbosity: TextVerbosity | None = Field(
        default=None,
        validation_alias=AliasChoices("MY_AGENTS_OPENAI_VERBOSITY"),
    )
    database_url: str = Field(
        default="sqlite+pysqlite:///:memory:",
        min_length=1,
        validation_alias=AliasChoices("MY_AGENTS_DATABASE_URL"),
    )
    test_database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MY_AGENTS_TEST_DATABASE_URL"),
    )
    auto_create_tables: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("MY_AGENTS_AUTO_CREATE_TABLES"),
    )
    session_cookie_name: str = Field(
        default="my_agents_session",
        min_length=1,
        validation_alias=AliasChoices("MY_AGENTS_SESSION_COOKIE_NAME"),
    )
    session_cookie_secure: bool = Field(
        default=True,
        validation_alias=AliasChoices("MY_AGENTS_SESSION_COOKIE_SECURE"),
    )
    session_cookie_samesite: SameSitePolicy = Field(
        default="lax",
        validation_alias=AliasChoices("MY_AGENTS_SESSION_COOKIE_SAMESITE"),
    )
    csrf_header_name: str = Field(
        default="X-CSRF-Token",
        min_length=1,
        validation_alias=AliasChoices("MY_AGENTS_CSRF_HEADER_NAME"),
    )
    auth_dev_outbox_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED"),
    )
    deployment_environment: DeploymentEnvironment = Field(
        default="local",
        validation_alias=AliasChoices("MY_AGENTS_DEPLOYMENT_ENVIRONMENT"),
    )
    auth_email_mode: AuthEmailMode = Field(
        default="local",
        validation_alias=AliasChoices("MY_AGENTS_AUTH_EMAIL_MODE"),
    )
    auth_public_app_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL"),
    )
    auth_smtp_host: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_SMTP_HOST"),
    )
    auth_smtp_port: int = Field(
        default=587,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_SMTP_PORT"),
    )
    auth_smtp_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_SMTP_USERNAME"),
    )
    auth_smtp_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_SMTP_PASSWORD"),
    )
    auth_smtp_from_email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_SMTP_FROM_EMAIL"),
    )
    auth_smtp_use_starttls: bool = Field(
        default=True,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_SMTP_USE_STARTTLS"),
    )
    auth_smtp_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_SMTP_TIMEOUT_SECONDS"),
    )
    cors_allowed_origins: str = Field(
        default="",
        validation_alias=AliasChoices("MY_AGENTS_CORS_ALLOWED_ORIGINS"),
    )
    auth_abuse_protection_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_ABUSE_PROTECTION_ENABLED"),
    )
    auth_abuse_max_attempts: int = Field(
        default=20,
        ge=1,
        le=1000,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_ABUSE_MAX_ATTEMPTS"),
    )
    auth_abuse_window_seconds: int = Field(
        default=900,
        ge=1,
        le=86400,
        validation_alias=AliasChoices("MY_AGENTS_AUTH_ABUSE_WINDOW_SECONDS"),
    )

    @field_validator("openai_model")
    @classmethod
    def openai_model_must_not_be_blank(cls, value: str) -> str:
        """Keep model selection env-driven without accepting empty model slugs."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("MY_AGENTS_OPENAI_MODEL must not be blank")
        return stripped

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def blank_openai_api_key_is_missing(cls, value: object) -> object:
        """Treat a blank API key like a missing key so OpenAI mode fails clearly."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("openai_reasoning_effort", "openai_verbosity", mode="before")
    @classmethod
    def empty_optional_string_is_none(cls, value: object) -> object:
        """Allow optional `.env` tuning keys to be present but blank."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def cors_allowed_origins_must_not_include_wildcard(cls, value: str) -> str:
        """Keep browser-cookie CORS explicit; wildcard plus credentials is unsafe."""
        if any("*" in origin.strip() for origin in value.split(",")):
            raise ValueError("MY_AGENTS_CORS_ALLOWED_ORIGINS must list explicit origins")
        return value

    @field_validator("database_url", "session_cookie_name", "csrf_header_name")
    @classmethod
    def service_setting_must_not_be_blank(cls, value: str) -> str:
        """Reject blank service-foundation settings before request handling."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("service foundation settings must not be blank")
        return stripped

    @field_validator(
        "test_database_url",
        "auto_create_tables",
        "auth_public_app_base_url",
        "auth_smtp_host",
        "auth_smtp_username",
        "auth_smtp_password",
        "auth_smtp_from_email",
        mode="before",
    )
    @classmethod
    def blank_optional_service_setting_is_missing(cls, value: object) -> object:
        """Treat blank optional service settings as absent."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "auth_public_app_base_url",
        "auth_smtp_host",
        "auth_smtp_username",
        "auth_smtp_from_email",
    )
    @classmethod
    def optional_service_string_is_stripped(cls, value: str | None) -> str | None:
        """Normalize optional deployment/auth strings without accepting blanks."""
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("http://") or stripped.startswith("https://"):
            return stripped.rstrip("/")
        return stripped

    @field_validator("auth_public_app_base_url")
    @classmethod
    def auth_public_app_base_url_must_be_http_url(cls, value: str | None) -> str | None:
        """Require real browser URLs for verification/reset links."""
        if value is None:
            return None
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL must be an http(s) URL")
        return value

    def cors_allowed_origin_list(self) -> tuple[str, ...]:
        """Return configured frontend origins as normalized explicit origins."""
        return tuple(
            origin.strip().rstrip("/")
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        )

    def should_auto_create_tables(self) -> bool:
        """Return whether runtime startup should create tables for this database URL."""
        if self.auto_create_tables is not None:
            return self.auto_create_tables
        return self.database_url == "sqlite+pysqlite:///:memory:"

    @model_validator(mode="after")
    def validate_runtime_security_settings(self) -> Settings:
        """Fail fast when runtime settings would make auth/session behavior unsafe."""
        if self.response_mode == "openai" and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when MY_AGENTS_RESPONSE_MODE=openai")
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError(
                "MY_AGENTS_SESSION_COOKIE_SECURE=true is required when "
                "MY_AGENTS_SESSION_COOKIE_SAMESITE=none"
            )
        if self.deployment_environment == "production":
            if self.auth_dev_outbox_enabled:
                raise ValueError(
                    "MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED=false is required when "
                    "MY_AGENTS_DEPLOYMENT_ENVIRONMENT=production"
                )
            if self.auth_email_mode == "local":
                raise ValueError(
                    "MY_AGENTS_AUTH_EMAIL_MODE=smtp is required when "
                    "MY_AGENTS_DEPLOYMENT_ENVIRONMENT=production"
                )
        if self.auth_email_mode == "smtp":
            missing = [
                env_name
                for env_name, value in (
                    ("MY_AGENTS_AUTH_SMTP_HOST", self.auth_smtp_host),
                    ("MY_AGENTS_AUTH_SMTP_FROM_EMAIL", self.auth_smtp_from_email),
                    ("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL", self.auth_public_app_base_url),
                )
                if value is None
            ]
            if missing:
                raise ValueError("MY_AGENTS_AUTH_EMAIL_MODE=smtp requires " + ", ".join(missing))
        return self

    def openai_api_key_value(self) -> str | None:
        """Return the raw API key only at the SDK boundary."""
        if self.openai_api_key is None:
            return None
        return self.openai_api_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Return cached process settings for request handling."""
    env_file = os.environ.get("MY_AGENTS_ENV_FILE")
    if env_file == "":
        return Settings(_env_file=None)
    if env_file is not None:
        return Settings(_env_file=env_file)
    return Settings()
