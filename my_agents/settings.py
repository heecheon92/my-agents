"""Runtime configuration for local and OpenAI-backed assistant behavior."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ResponseMode = Literal["deterministic", "openai"]
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]
TextVerbosity = Literal["low", "medium", "high"]


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
        default="deterministic",
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
        default=300,
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

    @field_validator("openai_model")
    @classmethod
    def openai_model_must_not_be_blank(cls, value: str) -> str:
        """Keep model selection env-driven without accepting empty model slugs."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("MY_AGENTS_OPENAI_MODEL must not be blank")
        return stripped

    @field_validator("openai_reasoning_effort", "openai_verbosity", mode="before")
    @classmethod
    def empty_optional_string_is_none(cls, value: object) -> object:
        """Allow optional `.env` tuning keys to be present but blank."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

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
