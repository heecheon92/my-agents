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


def test_embedding_settings_default_to_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.delenv("MY_AGENTS_EMBEDDING_MODE", raising=False)
    monkeypatch.delenv("MY_AGENTS_OPENAI_EMBEDDING_DIMENSIONS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.embedding_mode == "deterministic"
    assert settings.openai_embedding_model == "text-embedding-3-small"
    assert settings.openai_embedding_dimensions is None
    assert settings.embedding_batch_size == 32
    assert settings.openai_embedding_timeout_seconds == 30
    assert settings.docling_accelerator == "cpu"
    assert settings.docling_ocr_enabled is False
    assert settings.docling_timeout_seconds == 30
    assert settings.docling_threads == 4
    assert settings.tesseract_enabled is True
    assert settings.tesseract_languages == "kor+eng"
    assert settings.tesseract_psm == 6
    assert settings.tesseract_render_scale == 3.0
    assert settings.tesseract_timeout_seconds == 15
    assert settings.tesseract_max_pages == 3
    assert settings.document_metadata_enrichment_mode == "auto"
    assert settings.document_metadata_model is None
    assert settings.document_metadata_max_input_chars == 24000
    assert settings.ingestion_execution_mode == "in_process_thread"
    assert settings.ingestion_worker_poll_interval_seconds == 2.0
    assert settings.ingestion_worker_batch_size == 1
    assert settings.document_upload_concurrency == 3
    assert settings.active_run_stale_after_seconds == 120
    assert settings.metrics_enabled is False


def test_ingestion_worker_settings_accept_external_worker_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_INGESTION_EXECUTION_MODE", "external_worker")
    monkeypatch.setenv("MY_AGENTS_INGESTION_WORKER_POLL_INTERVAL_SECONDS", "0.5")
    monkeypatch.setenv("MY_AGENTS_INGESTION_WORKER_BATCH_SIZE", "3")
    monkeypatch.setenv("MY_AGENTS_DOCUMENT_UPLOAD_CONCURRENCY", "4")
    monkeypatch.setenv("MY_AGENTS_ACTIVE_RUN_STALE_AFTER_SECONDS", "45")

    settings = Settings(_env_file=None)

    assert settings.ingestion_execution_mode == "external_worker"
    assert settings.ingestion_worker_poll_interval_seconds == 0.5
    assert settings.ingestion_worker_batch_size == 3
    assert settings.document_upload_concurrency == 4
    assert settings.active_run_stale_after_seconds == 45


def test_document_metadata_openai_mode_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DOCUMENT_METADATA_ENRICHMENT_MODE", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MY_AGENTS_OPENAI_API_KEY", raising=False)

    with pytest.raises(ValidationError, match="DOCUMENT_METADATA_ENRICHMENT_MODE=openai"):
        Settings(_env_file=None)


def test_document_metadata_settings_accept_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DOCUMENT_METADATA_ENRICHMENT_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MY_AGENTS_DOCUMENT_METADATA_MODEL", "gpt-metadata")
    monkeypatch.setenv("MY_AGENTS_DOCUMENT_METADATA_MAX_INPUT_CHARS", "48000")

    settings = Settings(_env_file=None)

    assert settings.document_metadata_enrichment_mode == "openai"
    assert settings.document_metadata_model == "gpt-metadata"
    assert settings.document_metadata_max_input_chars == 48000


def test_openai_embedding_mode_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_EMBEDDING_MODE", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MY_AGENTS_OPENAI_API_KEY", raising=False)

    with pytest.raises(ValidationError, match="MY_AGENTS_EMBEDDING_MODE=openai"):
        Settings(_env_file=None)


def test_openai_embedding_settings_accept_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_EMBEDDING_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MY_AGENTS_OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("MY_AGENTS_OPENAI_EMBEDDING_DIMENSIONS", "256")
    monkeypatch.setenv("MY_AGENTS_EMBEDDING_BATCH_SIZE", "8")
    monkeypatch.setenv("MY_AGENTS_OPENAI_EMBEDDING_TIMEOUT_SECONDS", "12")

    settings = Settings(_env_file=None)

    assert settings.embedding_mode == "openai"
    assert settings.openai_embedding_model == "text-embedding-3-large"
    assert settings.openai_embedding_dimensions == 256
    assert settings.embedding_batch_size == 8
    assert settings.openai_embedding_timeout_seconds == 12


def test_docling_settings_accept_production_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DOCLING_ACCELERATOR", "cuda")
    monkeypatch.setenv("MY_AGENTS_DOCLING_OCR_ENABLED", "true")
    monkeypatch.setenv("MY_AGENTS_DOCLING_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("MY_AGENTS_DOCLING_THREADS", "8")

    settings = Settings(_env_file=None)

    assert settings.docling_accelerator == "cuda"
    assert settings.docling_ocr_enabled is True
    assert settings.docling_timeout_seconds == 45
    assert settings.docling_threads == 8


def test_tesseract_settings_accept_ocr_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_TESSERACT_ENABLED", "false")
    monkeypatch.setenv("MY_AGENTS_TESSERACT_LANGUAGES", "eng")
    monkeypatch.setenv("MY_AGENTS_TESSERACT_PSM", "11")
    monkeypatch.setenv("MY_AGENTS_TESSERACT_RENDER_SCALE", "2.5")
    monkeypatch.setenv("MY_AGENTS_TESSERACT_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("MY_AGENTS_TESSERACT_MAX_PAGES", "4")

    settings = Settings(_env_file=None)

    assert settings.tesseract_enabled is False
    assert settings.tesseract_languages == "eng"
    assert settings.tesseract_psm == 11
    assert settings.tesseract_render_scale == 2.5
    assert settings.tesseract_timeout_seconds == 30
    assert settings.tesseract_max_pages == 4


def test_docling_settings_reject_unknown_accelerator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DOCLING_ACCELERATOR", "metal")

    with pytest.raises(ValidationError, match="DOCLING_ACCELERATOR"):
        Settings(_env_file=None)


def test_service_foundation_settings_have_safe_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.delenv("MY_AGENTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("MY_AGENTS_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("MY_AGENTS_ACCOUNT_SIGNUP_AUTO_APPROVAL", raising=False)
    monkeypatch.delenv("MY_AGENTS_GUEST_CODE_AUTO_APPROVAL", raising=False)

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
    assert settings.account_signup_auto_approval is False
    assert settings.auth_public_app_base_url is None
    assert settings.auth_smtp_host is None
    assert settings.auth_smtp_from_email is None
    assert settings.resend_api_key is None
    assert settings.resend_api_url == "https://api.resend.com/emails"
    assert settings.auth_abuse_protection_enabled is True
    assert settings.auth_abuse_max_attempts == 20
    assert settings.auth_abuse_window_seconds == 900
    assert settings.auth_password_hash_time_cost == 2
    assert settings.auth_password_hash_memory_cost_kib == 19_456
    assert settings.auth_password_hash_parallelism == 1
    assert settings.guest_access_enabled is False
    assert settings.guest_code_auto_approval is False
    assert settings.guest_code_ttl_seconds == 900
    assert settings.guest_access_ttl_seconds == 86400
    assert settings.guest_max_conversations == 1
    assert settings.guest_max_prompts == 5
    assert settings.guest_max_document_uploads == 3
    assert settings.active_run_stale_after_seconds == 120


def test_service_foundation_settings_accept_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "postgresql+psycopg://app:pw@db/app")
    monkeypatch.setenv(
        "MY_AGENTS_TEST_DATABASE_URL",
        "postgresql+psycopg://app:pw@db/test_app",
    )
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_NAME", "demo_session")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SAMESITE", "strict")
    monkeypatch.setenv("MY_AGENTS_CSRF_HEADER_NAME", "X-Demo-CSRF")
    monkeypatch.setenv("MY_AGENTS_DEPLOYMENT_ENVIRONMENT", "preview")
    monkeypatch.setenv("MY_AGENTS_AUTH_EMAIL_MODE", "smtp")
    monkeypatch.setenv("MY_AGENTS_AUTH_SIGNUP_ENABLED", "false")
    monkeypatch.setenv("MY_AGENTS_ACCOUNT_SIGNUP_AUTO_APPROVAL", "true")
    monkeypatch.setenv("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL", "https://demo.example.com/")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_PORT", "2525")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_USERNAME", "smtp-user")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_PASSWORD", "smtp-password")
    monkeypatch.setenv("MY_AGENTS_AUTH_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setenv("MY_AGENTS_RESEND_API_KEY", "resend-api-key")
    monkeypatch.setenv("MY_AGENTS_RESEND_API_URL", "https://api.resend.test/emails/")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_USE_STARTTLS", "false")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("MY_AGENTS_AUTH_ABUSE_PROTECTION_ENABLED", "false")
    monkeypatch.setenv("MY_AGENTS_AUTH_ABUSE_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("MY_AGENTS_AUTH_ABUSE_WINDOW_SECONDS", "120")
    monkeypatch.setenv("MY_AGENTS_AUTH_PASSWORD_HASH_TIME_COST", "3")
    monkeypatch.setenv("MY_AGENTS_AUTH_PASSWORD_HASH_MEMORY_COST_KIB", "32768")
    monkeypatch.setenv("MY_AGENTS_AUTH_PASSWORD_HASH_PARALLELISM", "2")
    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true")
    monkeypatch.setenv("MY_AGENTS_GUEST_CODE_AUTO_APPROVAL", "true")
    monkeypatch.setenv("MY_AGENTS_GUEST_CODE_TTL_SECONDS", "600")
    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_TTL_SECONDS", "86400")
    monkeypatch.setenv("MY_AGENTS_GUEST_MAX_CONVERSATIONS", "1")
    monkeypatch.setenv("MY_AGENTS_GUEST_MAX_PROMPTS", "5")
    monkeypatch.setenv("MY_AGENTS_GUEST_MAX_DOCUMENT_UPLOADS", "3")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://app:pw@db/app"
    assert settings.test_database_url == "postgresql+psycopg://app:pw@db/test_app"
    assert settings.auto_create_tables is None
    assert settings.should_auto_create_tables() is False
    assert settings.session_cookie_name == "demo_session"
    assert settings.session_cookie_secure is False
    assert settings.session_cookie_samesite == "strict"
    assert settings.csrf_header_name == "X-Demo-CSRF"
    assert settings.deployment_environment == "preview"
    assert settings.auth_email_mode == "smtp"
    assert settings.auth_signup_enabled is False
    assert settings.account_signup_auto_approval is True
    assert settings.auth_public_app_base_url == "https://demo.example.com"
    assert settings.auth_smtp_host == "smtp.example.com"
    assert settings.auth_smtp_port == 2525
    assert settings.auth_smtp_username == "smtp-user"
    assert settings.auth_smtp_password is not None
    assert settings.auth_smtp_password.get_secret_value() == "smtp-password"
    assert settings.auth_smtp_from_email == "noreply@example.com"
    assert settings.resend_api_key is not None
    assert settings.resend_api_key.get_secret_value() == "resend-api-key"
    assert settings.resend_api_url == "https://api.resend.test/emails"
    assert settings.auth_smtp_use_starttls is False
    assert settings.auth_smtp_timeout_seconds == 5
    assert settings.auth_abuse_protection_enabled is False
    assert settings.auth_abuse_max_attempts == 5
    assert settings.auth_abuse_window_seconds == 120
    assert settings.auth_password_hash_time_cost == 3
    assert settings.auth_password_hash_memory_cost_kib == 32_768
    assert settings.auth_password_hash_parallelism == 2
    assert settings.guest_access_enabled is True
    assert settings.guest_code_auto_approval is True
    assert settings.guest_code_ttl_seconds == 600
    assert settings.guest_access_ttl_seconds == 86400
    assert settings.guest_max_conversations == 1
    assert settings.guest_max_prompts == 5
    assert settings.guest_max_document_uploads == 3


def test_active_run_stale_threshold_accepts_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_ACTIVE_RUN_STALE_AFTER_SECONDS", "5")

    settings = Settings(_env_file=None)

    assert settings.active_run_stale_after_seconds == 5


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
        "http://localhost:3000/, https://demo.example.com",
    )

    settings = Settings(_env_file=None)

    assert settings.cors_allowed_origin_list() == (
        "http://localhost:3000",
        "https://demo.example.com",
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
    monkeypatch.setenv("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL", "demo.example.com")

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


def test_debug_knowledge_context_logging_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DEBUG_KNOWLEDGE_CONTEXT_LOGGING", "true")

    settings = Settings(_env_file=None)

    assert settings.debug_knowledge_context_logging is True


def test_debug_retrieval_timing_logging_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DEBUG_RETRIEVAL_TIMING_LOGGING", "true")

    settings = Settings(_env_file=None)

    assert settings.debug_retrieval_timing_logging is True


def test_debug_ingestion_timing_logging_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DEBUG_INGESTION_TIMING_LOGGING", "true")

    settings = Settings(_env_file=None)

    assert settings.debug_ingestion_timing_logging is True


def test_metrics_enabled_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_METRICS_ENABLED", "true")

    settings = Settings(_env_file=None)

    assert settings.metrics_enabled is True


def test_resend_http_email_mode_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_AUTH_EMAIL_MODE", "resend_http")
    monkeypatch.setenv("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL", "https://demo.example.com")
    monkeypatch.setenv("MY_AGENTS_AUTH_FROM_EMAIL", "noreply@example.com")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("MY_AGENTS_RESEND_API_KEY", raising=False)

    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        Settings(_env_file=None)
