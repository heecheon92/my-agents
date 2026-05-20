"""Runtime settings tests for env-driven provider selection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from my_agents.settings import Settings, get_settings


def test_settings_default_to_openai_when_api_key_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_AGENTS_RESPONSE_MODE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("MY_AGENTS_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MY_AGENTS_OPENAI_MODEL", raising=False)

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
    assert settings.auto_create_tables is None
    assert settings.should_auto_create_tables() is True
    assert settings.session_cookie_name == "my_agents_session"
    assert settings.session_cookie_secure is True
    assert settings.session_cookie_samesite == "lax"
    assert settings.csrf_header_name == "X-CSRF-Token"
    assert settings.deployment_environment == "local"
    assert settings.auth_email_mode == "local"
    assert settings.auth_signup_enabled is True
    assert settings.auth_public_app_base_url is None
    assert settings.auth_smtp_host is None
    assert settings.auth_smtp_from_email is None
    assert settings.auth_abuse_protection_enabled is True
    assert settings.auth_abuse_max_attempts == 20
    assert settings.auth_abuse_window_seconds == 900


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
    monkeypatch.setenv("MY_AGENTS_DEPLOYMENT_ENVIRONMENT", "preview")
    monkeypatch.setenv("MY_AGENTS_AUTH_EMAIL_MODE", "smtp")
    monkeypatch.setenv("MY_AGENTS_AUTH_SIGNUP_ENABLED", "false")
    monkeypatch.setenv("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL", "https://portfolio.example.com/")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_PORT", "2525")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_USERNAME", "smtp-user")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_PASSWORD", "smtp-password")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_USE_STARTTLS", "false")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("MY_AGENTS_AUTH_ABUSE_PROTECTION_ENABLED", "false")
    monkeypatch.setenv("MY_AGENTS_AUTH_ABUSE_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("MY_AGENTS_AUTH_ABUSE_WINDOW_SECONDS", "120")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://app:pw@db/app"
    assert settings.test_database_url == "postgresql+psycopg://app:pw@db/test_app"
    assert settings.auto_create_tables is None
    assert settings.should_auto_create_tables() is False
    assert settings.session_cookie_name == "portfolio_session"
    assert settings.session_cookie_secure is False
    assert settings.session_cookie_samesite == "strict"
    assert settings.csrf_header_name == "X-Portfolio-CSRF"
    assert settings.deployment_environment == "preview"
    assert settings.auth_email_mode == "smtp"
    assert settings.auth_signup_enabled is False
    assert settings.auth_public_app_base_url == "https://portfolio.example.com"
    assert settings.auth_smtp_host == "smtp.example.com"
    assert settings.auth_smtp_port == 2525
    assert settings.auth_smtp_username == "smtp-user"
    assert settings.auth_smtp_password is not None
    assert settings.auth_smtp_password.get_secret_value() == "smtp-password"
    assert settings.auth_smtp_from_email == "noreply@example.com"
    assert settings.auth_smtp_use_starttls is False
    assert settings.auth_smtp_timeout_seconds == 5
    assert settings.auth_abuse_protection_enabled is False
    assert settings.auth_abuse_max_attempts == 5
    assert settings.auth_abuse_window_seconds == 120


def test_samesite_none_requires_secure_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")

    with pytest.raises(ValidationError, match="SAMESITE=none"):
        Settings(_env_file=None)


def test_service_foundation_settings_accept_auto_create_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "postgresql+psycopg://app:pw@db/app")
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")

    settings = Settings(_env_file=None)

    assert settings.auto_create_tables is True
    assert settings.should_auto_create_tables() is True


def test_cors_allowed_origins_parse_csv_and_strip_trailing_slashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv(
        "MY_AGENTS_CORS_ALLOWED_ORIGINS",
        "http://localhost:3000/, https://portfolio.example.com",
    )

    settings = Settings(_env_file=None)

    assert settings.cors_allowed_origin_list() == (
        "http://localhost:3000",
        "https://portfolio.example.com",
    )


@pytest.mark.parametrize("wildcard_origin", ["*", "https://*.example.com"])
def test_cors_allowed_origins_reject_wildcard_for_cookie_credentials(
    monkeypatch: pytest.MonkeyPatch,
    wildcard_origin: str,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_CORS_ALLOWED_ORIGINS", wildcard_origin)

    with pytest.raises(ValidationError, match="explicit origins"):
        Settings(_env_file=None)


def test_production_environment_rejects_dev_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setenv("MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED", "true")

    with pytest.raises(ValidationError, match="AUTH_DEV_OUTBOX_ENABLED=false"):
        Settings(_env_file=None)


def test_production_environment_requires_non_local_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DEPLOYMENT_ENVIRONMENT", "production")

    with pytest.raises(ValidationError, match="AUTH_EMAIL_MODE=smtp"):
        Settings(_env_file=None)


def test_smtp_email_mode_requires_provider_and_public_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_AUTH_EMAIL_MODE", "smtp")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_HOST", "smtp.example.com")

    with pytest.raises(ValidationError, match="AUTH_SMTP_FROM_EMAIL"):
        Settings(_env_file=None)


def test_public_app_base_url_must_be_http_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL", "portfolio.example.com")

    with pytest.raises(ValidationError, match="AUTH_PUBLIC_APP_BASE_URL"):
        Settings(_env_file=None)


def test_get_settings_can_ignore_local_dotenv_for_isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_ENV_FILE", "")
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    assert settings.session_cookie_secure is False


def test_get_settings_can_load_explicit_env_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        "MY_AGENTS_RESPONSE_MODE=deterministic\n"
        "MY_AGENTS_DATABASE_URL=sqlite+pysqlite:///./custom-test.sqlite3\n"
    )
    monkeypatch.setenv("MY_AGENTS_ENV_FILE", str(env_file))
    monkeypatch.delenv("MY_AGENTS_DATABASE_URL", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.database_url == "sqlite+pysqlite:///./custom-test.sqlite3"
