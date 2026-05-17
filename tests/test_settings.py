"""Runtime settings tests for env-driven provider selection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from my_agents.settings import Settings


def test_settings_default_to_openai_when_api_key_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_AGENTS_RESPONSE_MODE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("MY_AGENTS_OPENAI_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.response_mode == "openai"
    assert settings.openai_model == "gpt-5.5"
    assert settings.openai_api_key_value() == "test-key"


def test_default_openai_mode_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_AGENTS_RESPONSE_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MY_AGENTS_OPENAI_API_KEY", raising=False)

    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None)


def test_openai_mode_requires_standard_openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MY_AGENTS_OPENAI_API_KEY", raising=False)

    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None)


def test_openai_mode_rejects_blank_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    monkeypatch.delenv("MY_AGENTS_OPENAI_API_KEY", raising=False)

    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None)


def test_openai_settings_accept_model_and_tuning_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MY_AGENTS_OPENAI_MODEL", "gpt-5.5")
    monkeypatch.setenv("MY_AGENTS_OPENAI_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS", "123")
    monkeypatch.setenv("MY_AGENTS_OPENAI_REASONING_EFFORT", "low")
    monkeypatch.setenv("MY_AGENTS_OPENAI_VERBOSITY", "low")

    settings = Settings(_env_file=None)

    assert settings.response_mode == "openai"
    assert settings.openai_api_key_value() == "test-key"
    assert settings.openai_model == "gpt-5.5"
    assert settings.openai_timeout_seconds == 15
    assert settings.openai_max_output_tokens == 123
    assert settings.openai_reasoning_effort == "low"
    assert settings.openai_verbosity == "low"


def test_service_foundation_settings_have_safe_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.delenv("MY_AGENTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("MY_AGENTS_TEST_DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    assert settings.test_database_url is None
    assert settings.session_cookie_name == "my_agents_session"
    assert settings.session_cookie_secure is True
    assert settings.session_cookie_samesite == "lax"
    assert settings.csrf_header_name == "X-CSRF-Token"


def test_service_foundation_settings_accept_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "postgresql+psycopg://app:pw@db/app")
    monkeypatch.setenv(
        "MY_AGENTS_TEST_DATABASE_URL",
        "postgresql+psycopg://app:pw@db/test_app",
    )
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_NAME", "portfolio_session")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SAMESITE", "strict")
    monkeypatch.setenv("MY_AGENTS_CSRF_HEADER_NAME", "X-Portfolio-CSRF")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://app:pw@db/app"
    assert settings.test_database_url == "postgresql+psycopg://app:pw@db/test_app"
    assert settings.session_cookie_name == "portfolio_session"
    assert settings.session_cookie_secure is False
    assert settings.session_cookie_samesite == "strict"
    assert settings.csrf_header_name == "X-Portfolio-CSRF"
