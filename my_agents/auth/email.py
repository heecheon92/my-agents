"""Local auth email delivery boundary.

The current implementation is intentionally offline-only: it records messages in memory so
signup verification and password reset flows can be developed and tested without a paid
email provider. A production sender can replace this boundary later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

AuthEmailPurpose = Literal["email_verification", "password_reset"]


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


class AuthEmailSender(Protocol):
    """Minimal email sender protocol used by auth workflows."""

    def send_email_verification(self, *, recipient_email: str, token: str) -> None:
        """Send or record a signup email-verification token."""
        ...

    def send_password_reset(self, *, recipient_email: str, token: str) -> None:
        """Send or record a password-reset token."""
        ...


class InMemoryAuthEmailSender:
    """Development/test sender that records auth emails in process memory."""

    def __init__(self) -> None:
        self._messages: list[AuthEmailMessage] = []

    def send_email_verification(self, *, recipient_email: str, token: str) -> None:
        self._messages.append(
            AuthEmailMessage(
                recipient_email=recipient_email,
                purpose="email_verification",
                subject="Verify your my-agents email",
                body=(f"Use this local development token to verify your email: {token}"),
                token=token,
            )
        )

    def send_password_reset(self, *, recipient_email: str, token: str) -> None:
        self._messages.append(
            AuthEmailMessage(
                recipient_email=recipient_email,
                purpose="password_reset",
                subject="Reset your my-agents password",
                body=(f"Use this local development token to reset your password: {token}"),
                token=token,
            )
        )

    def messages(self) -> tuple[AuthEmailMessage, ...]:
        """Return recorded messages in delivery order."""
        return tuple(self._messages)

    def clear(self) -> None:
        """Clear recorded messages between tests or local smoke runs."""
        self._messages.clear()


_LOCAL_AUTH_EMAIL_SENDER = InMemoryAuthEmailSender()


def get_auth_email_sender() -> AuthEmailSender:
    """Return the configured auth email sender.

    For v0 this is a local in-memory sender. Keeping it behind a function makes a future
    Resend/AWS SES implementation a boundary change instead of an auth-service rewrite.
    """
    return _LOCAL_AUTH_EMAIL_SENDER


def get_local_auth_email_outbox() -> InMemoryAuthEmailSender:
    """Return the development outbox for tests and local smoke checks."""
    return _LOCAL_AUTH_EMAIL_SENDER


def reset_local_auth_email_outbox() -> None:
    """Clear the development outbox."""
    _LOCAL_AUTH_EMAIL_SENDER.clear()
