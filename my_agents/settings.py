"""Runtime configuration for local and OpenAI-backed assistant behavior."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ResponseMode = Literal["deterministic", "openai"]
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]
TextVerbosity = Literal["low", "medium", "high"]
SameSitePolicy = Literal["lax", "strict", "none"]


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

    @field_validator("database_url", "session_cookie_name", "csrf_header_name")
    @classmethod
    def service_setting_must_not_be_blank(cls, value: str) -> str:
        """Reject blank service-foundation settings before request handling."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("service foundation settings must not be blank")
        return stripped

    @field_validator("test_database_url", "auto_create_tables", mode="before")
    @classmethod
    def blank_optional_service_setting_is_missing(cls, value: object) -> object:
        """Treat blank optional service settings as absent."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def should_auto_create_tables(self) -> bool:
        """Return whether runtime startup should create tables for this database URL."""
        if self.auto_create_tables is not None:
            return self.auto_create_tables
        return self.database_url == "sqlite+pysqlite:///:memory:"

    @model_validator(mode="after")
    def require_api_key_when_openai_mode_is_enabled(self) -> Settings:
        """Fail fast with a useful local error when OpenAI mode lacks credentials."""
        if self.response_mode == "openai" and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when MY_AGENTS_RESPONSE_MODE=openai")
        return self

    def openai_api_key_value(self) -> str | None:
        """Return the raw API key only at the SDK boundary."""
        if self.openai_api_key is None:
            return None
        return self.openai_api_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Return cached process settings for request handling."""
    return Settings()
