"""Local auth abuse-protection boundary."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass


class AuthRateLimitExceededError(RuntimeError):
    """Raised when an auth action exceeds the configured local attempt budget."""


@dataclass(frozen=True)
class AuthAbuseConfig:
    """Small local attempt-budget configuration for auth endpoints."""

    enabled: bool
    max_attempts: int
    window_seconds: int


@dataclass
class _AttemptBucket:
    count: int
    reset_at: float


class AuthAbuseProtector:
    """In-process auth attempt limiter with a replaceable boundary.

    This v0 boundary intentionally avoids Redis or provider-specific infrastructure while
    still protecting high-risk auth flows in local/dev and single-process demos. Keys use
    digests so raw emails or request identifiers are not stored in bucket keys.
    """

    def __init__(self, config: AuthAbuseConfig) -> None:
        self._config = config
        self._buckets: dict[tuple[str, str], _AttemptBucket] = {}
        self._lock = threading.Lock()

    def assert_allowed(self, *, action: str, identifier: str) -> None:
        """Raise when an action/identifier has exhausted its current window."""
        if not self._config.enabled:
            return
        key = self._key(action=action, identifier=identifier)
        now = time.monotonic()
        with self._lock:
            bucket = self._active_bucket(key, now)
            if bucket is not None and bucket.count >= self._config.max_attempts:
                raise AuthRateLimitExceededError("too many auth attempts")

    def record_attempt(self, *, action: str, identifier: str) -> None:
        """Count one auth attempt for an action/identifier window."""
        if not self._config.enabled:
            return
        key = self._key(action=action, identifier=identifier)
        now = time.monotonic()
        with self._lock:
            bucket = self._active_bucket(key, now)
            if bucket is None:
                self._buckets[key] = _AttemptBucket(
                    count=1,
                    reset_at=now + self._config.window_seconds,
                )
                return
            bucket.count += 1

    def reset(self, *, action: str, identifier: str) -> None:
        """Clear an action/identifier bucket after a safe successful auth action."""
        if not self._config.enabled:
            return
        with self._lock:
            self._buckets.pop(self._key(action=action, identifier=identifier), None)

    def clear(self) -> None:
        """Clear all buckets between tests or local smoke runs."""
        with self._lock:
            self._buckets.clear()

    def _active_bucket(
        self,
        key: tuple[str, str],
        now: float,
    ) -> _AttemptBucket | None:
        bucket = self._buckets.get(key)
        if bucket is None:
            return None
        if bucket.reset_at <= now:
            self._buckets.pop(key, None)
            return None
        return bucket

    @staticmethod
    def _key(*, action: str, identifier: str) -> tuple[str, str]:
        normalized_identifier = identifier.strip().casefold()
        digest = hashlib.sha256(normalized_identifier.encode("utf-8")).hexdigest()
        return action, digest


_PROTECTOR: AuthAbuseProtector | None = None
_PROTECTOR_CONFIG: AuthAbuseConfig | None = None


def get_auth_abuse_protector(config: AuthAbuseConfig) -> AuthAbuseProtector:
    """Return a cached protector for the active settings-derived configuration."""
    global _PROTECTOR, _PROTECTOR_CONFIG
    if _PROTECTOR is None or _PROTECTOR_CONFIG != config:
        _PROTECTOR = AuthAbuseProtector(config)
        _PROTECTOR_CONFIG = config
    return _PROTECTOR


def reset_auth_abuse_protector() -> None:
    """Clear cached auth abuse buckets and configuration."""
    global _PROTECTOR, _PROTECTOR_CONFIG
    if _PROTECTOR is not None:
        _PROTECTOR.clear()
    _PROTECTOR = None
    _PROTECTOR_CONFIG = None
