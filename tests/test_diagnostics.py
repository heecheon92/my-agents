"""Deployment diagnostic logging tests."""

import logging

from my_agents.diagnostics import deploy_log, safe_database_url_summary, safe_email_context


def test_deploy_log_uses_permanent_marker_and_redacts_sensitive_values(caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.WARNING, logger="my_agents.deploy_diag")

    deploy_log("auth.test", api_key="sk-secret", email="hidden@example.com", ok=True)

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert message.startswith("DEPLOY_DIAG auth.test")
    assert "api_key=<redacted>" in message
    assert "sk-secret" not in message
    assert "hidden@example.com" in message


def test_safe_diagnostic_context_helpers_avoid_raw_secrets() -> None:
    assert safe_email_context("User@Example.com") == {
        "email_hash": "b4c9a289323b",
        "email_domain": "example.com",
    }
    assert safe_database_url_summary(
        "postgresql+psycopg://user:secret@example.internal/dbname?sslmode=require"
    ) == {
        "db_scheme": "postgresql+psycopg",
        "db_host": "example.internal",
        "db_name": "dbname",
    }
