"""Auth email sender tests."""

from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage

from fastapi.testclient import TestClient

from my_agents.auth import email as auth_email
from my_agents.auth.email import build_auth_email_sender, get_local_auth_email_outbox
from my_agents.settings import Settings

from .conftest import load_app


class FakeResendHttpResponse:
    """Small HTTP response double for Resend API tests."""

    def raise_for_status(self) -> None:
        return None


class FakeResendHttpClient:
    """Small httpx.Client test double that records Resend API requests."""

    posts: list[dict[str, object]] = []

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> FakeResendHttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
    ) -> FakeResendHttpResponse:
        type(self).posts.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": self.timeout,
            }
        )
        return FakeResendHttpResponse()


class FakeSmtp:
    """Small SMTP test double that records messages without network access."""

    sent_messages: list[EmailMessage] = []
    starttls_calls = 0
    login_calls: list[tuple[str, str]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def starttls(self) -> None:
        self.starttls_calls += 1
        type(self).starttls_calls += 1

    def login(self, username: str, password: str) -> None:
        type(self).login_calls.append((username, password))

    def send_message(self, message: EmailMessage) -> None:
        type(self).sent_messages.append(message)


def test_smtp_sender_builds_visitor_account_links(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(auth_email.smtplib, "SMTP", FakeSmtp)
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_AUTH_EMAIL_MODE="smtp",
        MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL="https://demo.example.com",
        MY_AGENTS_AUTH_SMTP_HOST="smtp.example.com",
        MY_AGENTS_AUTH_SMTP_PORT="2525",
        MY_AGENTS_AUTH_SMTP_USERNAME="smtp-user",
        MY_AGENTS_AUTH_SMTP_PASSWORD="smtp-password",
        MY_AGENTS_AUTH_SMTP_FROM_EMAIL="noreply@example.com",
    )
    sender = build_auth_email_sender(settings)

    sender.send_email_verification(recipient_email="visitor@example.com", token="verify token")
    sender.send_password_reset(recipient_email="visitor@example.com", token="reset token")

    assert FakeSmtp.starttls_calls == 2
    assert FakeSmtp.login_calls == [("smtp-user", "smtp-password")] * 2
    assert len(FakeSmtp.sent_messages) == 2
    verification = FakeSmtp.sent_messages[0]
    reset = FakeSmtp.sent_messages[1]
    assert verification["From"] == "noreply@example.com"
    assert verification["To"] == "visitor@example.com"
    assert "https://demo.example.com/verify-email?token=verify%20token" in (
        verification.get_content()
    )
    assert "https://demo.example.com/password-reset?token=reset%20token" in (reset.get_content())


def test_signup_uses_configured_smtp_sender_without_local_outbox(monkeypatch) -> None:  # noqa: ANN001
    FakeSmtp.sent_messages.clear()
    FakeSmtp.login_calls.clear()
    FakeSmtp.starttls_calls = 0
    monkeypatch.setattr(auth_email.smtplib, "SMTP", FakeSmtp)
    monkeypatch.setenv("MY_AGENTS_AUTH_EMAIL_MODE", "smtp")
    monkeypatch.setenv("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL", "https://demo.example.com")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_USERNAME", "smtp-user")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_PASSWORD", "smtp-password")
    monkeypatch.setenv("MY_AGENTS_AUTH_SMTP_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    client = TestClient(load_app())

    signup = client.post(
        "/auth/signup",
        json={"email": "smtp-signup@example.com", "password": "correct horse battery staple"},
    )

    assert signup.status_code == 201
    assert signup.json()["verification_email_sent"] is True
    assert len(FakeSmtp.sent_messages) == 1
    assert FakeSmtp.sent_messages[0]["To"] == "smtp-signup@example.com"
    assert "/verify-email?token=" in FakeSmtp.sent_messages[0].get_content()
    assert get_local_auth_email_outbox().messages() == ()
    assert client.get("/auth/dev/outbox").status_code == 404


def test_resend_http_sender_uses_https_api_links(monkeypatch) -> None:  # noqa: ANN001
    FakeResendHttpClient.posts.clear()
    monkeypatch.setattr(auth_email.httpx, "Client", FakeResendHttpClient)
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_AUTH_EMAIL_MODE="resend_http",
        MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL="https://demo.example.com",
        MY_AGENTS_AUTH_FROM_EMAIL="noreply@example.com",
        MY_AGENTS_RESEND_API_KEY="resend-api-key",
        MY_AGENTS_RESEND_API_URL="https://api.resend.test/emails",
        MY_AGENTS_AUTH_SMTP_TIMEOUT_SECONDS="7",
    )
    sender = build_auth_email_sender(settings)

    sender.send_email_verification(recipient_email="visitor@example.com", token="verify token")
    sender.send_password_reset(recipient_email="visitor@example.com", token="reset token")

    assert len(FakeResendHttpClient.posts) == 2
    verification = FakeResendHttpClient.posts[0]
    reset = FakeResendHttpClient.posts[1]
    assert verification["url"] == "https://api.resend.test/emails"
    assert verification["headers"] == {
        "Authorization": "Bearer resend-api-key",
        "Content-Type": "application/json",
    }
    assert verification["json"] == {
        "from": "noreply@example.com",
        "to": ["visitor@example.com"],
        "subject": "Verify your my-agents email",
        "text": (
            "Verify your my-agents account by opening this link:\n\n"
            "https://demo.example.com/verify-email?token=verify%20token\n\n"
            "If you did not create this account, you can ignore this email."
        ),
    }
    assert reset["json"] == {
        "from": "noreply@example.com",
        "to": ["visitor@example.com"],
        "subject": "Reset your my-agents password",
        "text": (
            "Reset your my-agents password by opening this link:\n\n"
            "https://demo.example.com/password-reset?token=reset%20token\n\n"
            "If you did not request a reset, you can ignore this email."
        ),
    }
    assert verification["timeout"] == 7


def test_resend_http_sender_sends_guest_access_code(monkeypatch) -> None:  # noqa: ANN001
    FakeResendHttpClient.posts.clear()
    monkeypatch.setattr(auth_email.httpx, "Client", FakeResendHttpClient)
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_AUTH_EMAIL_MODE="resend_http",
        MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL="https://demo.example.com",
        MY_AGENTS_AUTH_FROM_EMAIL="noreply@example.com",
        MY_AGENTS_RESEND_API_KEY="resend-api-key",
        MY_AGENTS_RESEND_API_URL="https://api.resend.test/emails",
    )
    sender = build_auth_email_sender(settings)

    sender.send_guest_access_code(
        recipient_email="guest@example.com",
        code="guest-code-123",
        expires_at=datetime(2026, 6, 6, 12, 30, tzinfo=UTC),
        language="en",
    )

    assert len(FakeResendHttpClient.posts) == 1
    assert FakeResendHttpClient.posts[0]["json"] == {
        "from": "noreply@example.com",
        "to": ["guest@example.com"],
        "subject": "Your my-agents guest access code",
        "text": (
            "Your my-agents guest demo access code is:\n\n"
            "guest-code-123\n\n"
            "This one-time code expires at 2026-06-06T12:30:00+00:00.\n"
            "After login, the guest session is limited to 24 hours, one conversation, "
            "five prompts, and three document uploads.\n\n"
            "If you did not request guest access, you can ignore this email."
        ),
    }


def test_guest_access_code_email_defaults_to_korean() -> None:
    sender = auth_email.InMemoryAuthEmailSender()

    sender.send_guest_access_code(
        recipient_email="guest@example.com",
        code="guest-code-123",
        expires_at=datetime(2026, 6, 6, 12, 30, tzinfo=UTC),
    )

    message = sender.messages()[0]
    assert message.subject == "my-agents 게스트 데모 접근 코드"
    assert "my-agents 게스트 데모 접근 코드는 다음과 같습니다." in message.body
    assert "guest-code-123" in message.body
    assert "대화 1개, 프롬프트 5개, 문서 업로드 3개" in message.body


def test_signup_uses_resend_http_sender_without_local_outbox(monkeypatch) -> None:  # noqa: ANN001
    FakeResendHttpClient.posts.clear()
    monkeypatch.setattr(auth_email.httpx, "Client", FakeResendHttpClient)
    monkeypatch.setenv("MY_AGENTS_AUTH_EMAIL_MODE", "resend_http")
    monkeypatch.setenv("MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL", "https://demo.example.com")
    monkeypatch.setenv("MY_AGENTS_AUTH_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setenv("MY_AGENTS_RESEND_API_KEY", "resend-api-key")
    monkeypatch.setenv("MY_AGENTS_RESEND_API_URL", "https://api.resend.test/emails")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    client = TestClient(load_app())

    signup = client.post(
        "/auth/signup",
        json={"email": "resend-signup@example.com", "password": "correct horse battery staple"},
    )

    assert signup.status_code == 201
    assert signup.json()["verification_email_sent"] is True
    assert len(FakeResendHttpClient.posts) == 1
    assert FakeResendHttpClient.posts[0]["json"]["to"] == ["resend-signup@example.com"]
    assert "/verify-email?token=" in FakeResendHttpClient.posts[0]["json"]["text"]
    assert get_local_auth_email_outbox().messages() == ()
    assert client.get("/auth/dev/outbox").status_code == 404
