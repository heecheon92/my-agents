"""Auth email delivery boundary.

The local implementation records messages in memory so signup verification and password
reset flows can be developed and tested without a paid email provider. The SMTP
implementation supports preview/public visitor accounts through a generic provider relay
without making unit tests depend on network access.
"""

from __future__ import annotations

import hashlib
import json
import logging
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import quote

import httpx

from my_agents.diagnostics import deploy_log

if TYPE_CHECKING:
    from my_agents.settings import Settings

logger = logging.getLogger(__name__)

AuthEmailPurpose = Literal["email_verification", "password_reset", "guest_access_code"]
AuthEmailLanguage = Literal["ko", "en"]


@dataclass(frozen=True)
class AuthEmailMessage:
    """A locally recorded auth email.

    The raw token is included only because this sender is a development/test boundary.
    Real providers should send the token/link and should not persist raw token material.
    """

    recipient_email: str
    purpose: AuthEmailPurpose
    subject: str
    body: str
    token: str


@dataclass(frozen=True)
class RenderedAuthEmail:
    """Localized auth email content ready for delivery."""

    subject: str
    body: str


class AuthEmailSender(Protocol):
    """Minimal email sender protocol used by auth workflows."""

    def send_email_verification(
        self,
        *,
        recipient_email: str,
        token: str,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        """Send or record a signup email-verification token."""
        ...

    def send_password_reset(
        self,
        *,
        recipient_email: str,
        token: str,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        """Send or record a password-reset token."""
        ...

    def send_guest_access_code(
        self,
        *,
        recipient_email: str,
        code: str,
        expires_at: datetime,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        """Send or record an operator-issued guest access code."""
        ...


class InMemoryAuthEmailSender:
    """Development/test sender that records auth emails in process memory."""

    def __init__(self) -> None:
        self._messages: list[AuthEmailMessage] = []

    def send_email_verification(
        self,
        *,
        recipient_email: str,
        token: str,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        message = _auth_email_message(
            purpose="email_verification",
            language=language,
            link=_action_link("/verify-email", token, base_url=""),
        )
        self._messages.append(
            AuthEmailMessage(
                recipient_email=recipient_email,
                purpose="email_verification",
                subject=message.subject,
                body=message.body,
                token=token,
            )
        )

    def send_password_reset(
        self,
        *,
        recipient_email: str,
        token: str,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        message = _auth_email_message(
            purpose="password_reset",
            language=language,
            link=_action_link("/password-reset", token, base_url=""),
        )
        self._messages.append(
            AuthEmailMessage(
                recipient_email=recipient_email,
                purpose="password_reset",
                subject=message.subject,
                body=message.body,
                token=token,
            )
        )

    def send_guest_access_code(
        self,
        *,
        recipient_email: str,
        code: str,
        expires_at: datetime,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        message = _guest_access_code_message(
            code=code,
            expires_at=expires_at,
            language=language,
        )
        self._messages.append(
            AuthEmailMessage(
                recipient_email=recipient_email,
                purpose="guest_access_code",
                subject=message.subject,
                body=message.body,
                token=code,
            )
        )

    def messages(self) -> tuple[AuthEmailMessage, ...]:
        """Return recorded messages in delivery order."""
        return tuple(self._messages)

    def clear(self) -> None:
        """Clear recorded messages between tests or local smoke runs."""
        self._messages.clear()


class SmtpAuthEmailSender:
    """SMTP-backed auth email sender for preview/public visitor account flows.

    The sender creates verification/reset links from the configured public frontend URL and
    sends them through a generic SMTP relay. It does not store raw tokens in process memory;
    local tests and deterministic demos should continue using `InMemoryAuthEmailSender`.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_email: str,
        public_app_base_url: str,
        username: str | None = None,
        password: str | None = None,
        use_starttls: bool = True,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._from_email = from_email
        self._public_app_base_url = public_app_base_url.rstrip("/")
        self._username = username
        self._password = password
        self._use_starttls = use_starttls
        self._timeout_seconds = timeout_seconds

    def send_email_verification(
        self,
        *,
        recipient_email: str,
        token: str,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        link = self._action_link("/verify-email", token)
        message = _auth_email_message(
            purpose="email_verification",
            language=language,
            link=link,
        )
        self._send(
            recipient_email=recipient_email,
            subject=message.subject,
            body=message.body,
        )

    def send_password_reset(
        self,
        *,
        recipient_email: str,
        token: str,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        link = self._action_link("/password-reset", token)
        message = _auth_email_message(
            purpose="password_reset",
            language=language,
            link=link,
        )
        self._send(
            recipient_email=recipient_email,
            subject=message.subject,
            body=message.body,
        )

    def send_guest_access_code(
        self,
        *,
        recipient_email: str,
        code: str,
        expires_at: datetime,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        message = _guest_access_code_message(
            code=code,
            expires_at=expires_at,
            language=language,
        )
        self._send(
            recipient_email=recipient_email,
            subject=message.subject,
            body=message.body,
        )

    def _send(self, *, recipient_email: str, subject: str, body: str) -> None:
        context = _email_log_context(recipient_email)
        deploy_log(
            "auth.email.smtp.start",
            host=self._host,
            port=self._port,
            from_domain=_email_domain(self._from_email),
            starttls=self._use_starttls,
            **context,
        )
        logger.info(
            "auth_email.smtp.send.start host=%s port=%s from_domain=%s recipient_hash=%s "
            "recipient_domain=%s starttls=%s",
            self._host,
            self._port,
            _email_domain(self._from_email),
            context["email_hash"],
            context["email_domain"],
            self._use_starttls,
        )
        message = EmailMessage()
        message["From"] = self._from_email
        message["To"] = recipient_email
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout_seconds) as smtp:
                if self._use_starttls:
                    smtp.starttls()
                if self._username is not None and self._password is not None:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except Exception as exc:
            deploy_log(
                "auth.email.smtp.failed",
                host=self._host,
                port=self._port,
                from_domain=_email_domain(self._from_email),
                error_class=exc.__class__.__name__,
                **context,
            )
            logger.error(
                "auth_email.smtp.send.failed host=%s port=%s from_domain=%s recipient_hash=%s "
                "recipient_domain=%s error_class=%s",
                self._host,
                self._port,
                _email_domain(self._from_email),
                context["email_hash"],
                context["email_domain"],
                exc.__class__.__name__,
            )
            raise
        deploy_log(
            "auth.email.smtp.completed",
            host=self._host,
            port=self._port,
            from_domain=_email_domain(self._from_email),
            **context,
        )
        logger.info(
            "auth_email.smtp.send.completed host=%s port=%s from_domain=%s recipient_hash=%s "
            "recipient_domain=%s",
            self._host,
            self._port,
            _email_domain(self._from_email),
            context["email_hash"],
            context["email_domain"],
        )

    def _action_link(self, path: str, token: str) -> str:
        return f"{self._public_app_base_url}{path}?token={quote(token, safe='')}"


class ResendHttpAuthEmailSender:
    """Resend HTTP API auth email sender for hosts that block SMTP ports.

    This provider uses HTTPS/443 instead of SMTP/587, which keeps public-demo email
    delivery independent from host-level SMTP egress policies while preserving the same
    `AuthEmailSender` boundary as local and generic SMTP modes.
    """

    def __init__(
        self,
        *,
        api_key: str,
        from_email: str,
        public_app_base_url: str,
        api_url: str = "https://api.resend.com/emails",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._from_email = from_email
        self._public_app_base_url = public_app_base_url.rstrip("/")
        self._api_url = api_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def send_email_verification(
        self,
        *,
        recipient_email: str,
        token: str,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        link = self._action_link("/verify-email", token)
        message = _auth_email_message(
            purpose="email_verification",
            language=language,
            link=link,
        )
        self._send(
            recipient_email=recipient_email,
            subject=message.subject,
            body=message.body,
        )

    def send_password_reset(
        self,
        *,
        recipient_email: str,
        token: str,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        link = self._action_link("/password-reset", token)
        message = _auth_email_message(
            purpose="password_reset",
            language=language,
            link=link,
        )
        self._send(
            recipient_email=recipient_email,
            subject=message.subject,
            body=message.body,
        )

    def send_guest_access_code(
        self,
        *,
        recipient_email: str,
        code: str,
        expires_at: datetime,
        language: AuthEmailLanguage = "ko",
    ) -> None:
        message = _guest_access_code_message(
            code=code,
            expires_at=expires_at,
            language=language,
        )
        self._send(
            recipient_email=recipient_email,
            subject=message.subject,
            body=message.body,
        )

    def _send(self, *, recipient_email: str, subject: str, body: str) -> None:
        context = _email_log_context(recipient_email)
        deploy_log(
            "auth.email.resend_http.start",
            from_domain=_email_domain(self._from_email),
            **context,
        )
        logger.info(
            "auth_email.resend_http.send.start from_domain=%s recipient_hash=%s "
            "recipient_domain=%s",
            _email_domain(self._from_email),
            context["email_hash"],
            context["email_domain"],
        )
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(
                    self._api_url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": self._from_email,
                        "to": [recipient_email],
                        "subject": subject,
                        "text": body,
                    },
                )
                response.raise_for_status()
        except Exception as exc:
            deploy_log(
                "auth.email.resend_http.failed",
                from_domain=_email_domain(self._from_email),
                error_class=exc.__class__.__name__,
                **context,
            )
            logger.error(
                "auth_email.resend_http.send.failed from_domain=%s recipient_hash=%s "
                "recipient_domain=%s error_class=%s",
                _email_domain(self._from_email),
                context["email_hash"],
                context["email_domain"],
                exc.__class__.__name__,
            )
            raise
        deploy_log(
            "auth.email.resend_http.completed",
            from_domain=_email_domain(self._from_email),
            **context,
        )
        logger.info(
            "auth_email.resend_http.send.completed from_domain=%s recipient_hash=%s "
            "recipient_domain=%s",
            _email_domain(self._from_email),
            context["email_hash"],
            context["email_domain"],
        )

    def _action_link(self, path: str, token: str) -> str:
        return f"{self._public_app_base_url}{path}?token={quote(token, safe='')}"


def _email_log_context(email: str) -> dict[str, str]:
    normalized = email.strip().casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return {"email_hash": digest, "email_domain": _email_domain(normalized)}


def _email_domain(email: str) -> str:
    _, _, domain = email.strip().casefold().partition("@")
    return domain or "unknown"


def _action_link(path: str, token: str, *, base_url: str) -> str:
    return f"{base_url.rstrip('/')}{path}?token={quote(token, safe='')}"


def _guest_access_code_message(
    *, code: str, expires_at: datetime, language: AuthEmailLanguage
) -> RenderedAuthEmail:
    expires_at_utc = expires_at
    if expires_at_utc.tzinfo is None:
        expires_at_utc = expires_at_utc.replace(tzinfo=UTC)
    else:
        expires_at_utc = expires_at_utc.astimezone(UTC)
    return _auth_email_message(
        purpose="guest_access_code",
        language=language,
        code=code,
        expires_at=expires_at_utc.isoformat(),
    )


def _auth_email_message(
    *, purpose: AuthEmailPurpose, language: AuthEmailLanguage, **values: str
) -> RenderedAuthEmail:
    template = _auth_email_templates(language)[purpose]
    return RenderedAuthEmail(
        subject=template["subject"].format(**values),
        body=template["body"].format(**values),
    )


@lru_cache(maxsize=2)
def _auth_email_templates(language: AuthEmailLanguage) -> dict[AuthEmailPurpose, dict[str, str]]:
    template_path = resources.files("my_agents.auth.email_templates").joinpath(f"{language}.json")
    raw_templates = json.loads(template_path.read_text(encoding="utf-8"))
    return {
        purpose: {
            "subject": str(template["subject"]),
            "body": str(template["body"]),
        }
        for purpose, template in raw_templates.items()
    }


_LOCAL_AUTH_EMAIL_SENDER = InMemoryAuthEmailSender()


def get_auth_email_sender() -> AuthEmailSender:
    """Return the configured auth email sender.

    For v0 this is a local in-memory sender. Keeping it behind a function makes a future
    Resend/AWS SES implementation a boundary change instead of an auth-service rewrite.
    """
    return _LOCAL_AUTH_EMAIL_SENDER


def build_auth_email_sender(settings: Settings) -> AuthEmailSender:
    """Build the auth email sender for the active runtime settings."""
    if settings.auth_email_mode == "resend_http":
        api_key = settings.resend_api_key.get_secret_value() if settings.resend_api_key else ""
        return ResendHttpAuthEmailSender(
            api_key=api_key,
            from_email=settings.auth_smtp_from_email or "",
            public_app_base_url=settings.auth_public_app_base_url or "",
            api_url=settings.resend_api_url,
            timeout_seconds=settings.auth_smtp_timeout_seconds,
        )
    if settings.auth_email_mode == "smtp":
        password = (
            settings.auth_smtp_password.get_secret_value()
            if settings.auth_smtp_password is not None
            else None
        )
        return SmtpAuthEmailSender(
            host=settings.auth_smtp_host or "",
            port=settings.auth_smtp_port,
            from_email=settings.auth_smtp_from_email or "",
            public_app_base_url=settings.auth_public_app_base_url or "",
            username=settings.auth_smtp_username,
            password=password,
            use_starttls=settings.auth_smtp_use_starttls,
            timeout_seconds=settings.auth_smtp_timeout_seconds,
        )
    return _LOCAL_AUTH_EMAIL_SENDER


def get_local_auth_email_outbox() -> InMemoryAuthEmailSender:
    """Return the development outbox for tests and local smoke checks."""
    return _LOCAL_AUTH_EMAIL_SENDER


def reset_local_auth_email_outbox() -> None:
    """Clear the development outbox."""
    _LOCAL_AUTH_EMAIL_SENDER.clear()
