"""Temporary deployment diagnostics.

TEMP_DEPLOY_DIAG logs are intentionally unconditional and warning-level so hosted
runtime logs show enough request/DB/auth/email breadcrumbs while deployment is being
stabilized. Remove this module and its call sites once Render/Vercel/Neon wiring is
proven.
"""

from __future__ import annotations

import hashlib
import logging
from urllib.parse import urlsplit

logger = logging.getLogger("my_agents.temp_deploy_diag")


def deploy_log(event: str, **fields: object) -> None:
    """Emit one safe deployment diagnostic line without secrets."""
    field_text = " ".join(f"{key}={_safe_value(value)}" for key, value in fields.items())
    logger.warning("TEMP_DEPLOY_DIAG %s%s", event, f" {field_text}" if field_text else "")


def safe_email_context(email: str) -> dict[str, str]:
    """Return digest/domain only; never log a full email address."""
    normalized = email.strip().casefold()
    _, _, domain = normalized.partition("@")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return {"email_hash": digest, "email_domain": domain or "unknown"}


def safe_database_url_summary(database_url: str) -> dict[str, str]:
    """Return non-secret database URL metadata for diagnostics."""
    parsed = urlsplit(database_url)
    return {
        "db_scheme": parsed.scheme or "unknown",
        "db_host": parsed.hostname or "local",
        "db_name": parsed.path.lstrip("/") or "unknown",
    }


def safe_email_domain(email: str) -> str:
    """Return only the email domain."""
    _, _, domain = email.strip().casefold().partition("@")
    return domain or "unknown"


def _safe_value(value: object) -> str:
    text = str(value)
    for marker in ("password", "token", "secret", "key="):
        if marker in text.casefold():
            return "<redacted>"
    return text.replace("\n", "\\n").replace("\r", "\\r")
