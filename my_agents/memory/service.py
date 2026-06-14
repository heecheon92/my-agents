"""Service layer for opt-in, per-user long-term memory."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from my_agents.memory.models import (
    MemoryCategory,
    MemoryProvenanceType,
    MemorySensitivity,
    MemoryStatus,
    MemorySuggestionModel,
    MemorySuggestionStatus,
    UserMemoryModel,
    UserMemorySettingsModel,
)
from my_agents.memory.policy import (
    MemoryPolicyDecision,
    MemoryWriteMode,
    evaluate_memory_write,
    has_preference_shape,
)

MEMORY_NAMESPACE_KIND = "memories"
DEFAULT_SUGGESTION_TTL = timedelta(days=7)


class MemoryDisabledError(RuntimeError):
    """Raised when a memory write is attempted while user memory is disabled."""


class MemoryNotFoundError(LookupError):
    """Raised when a memory record is missing or belongs to another user."""


class MemoryPolicyError(ValueError):
    """Raised when a memory write violates category/sensitivity/provenance policy."""


class MemorySuggestionNotFoundError(LookupError):
    """Raised when a pending suggestion is missing or belongs to another user."""


class MemorySuggestionUnavailableError(RuntimeError):
    """Raised when a suggestion can no longer be confirmed or rejected."""


class UserMemoryService:
    """Owns durable user-memory settings, suggestions, and records.

    The table shape mirrors LangGraph Store's namespace/key/value model while keeping the
    Product DB as the source of truth for opt-in settings, provenance, and user isolation.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def _expire_user_pending_suggestions(self, user_id: str) -> None:
        """Scrub expired pending suggestions during normal user-scoped memory traffic."""
        self.expire_pending_suggestions(user_id=user_id)

    def get_settings(self, user_id: str) -> UserMemorySettingsModel | None:
        """Return stored memory settings without creating a row."""
        return self._db.scalar(
            select(UserMemorySettingsModel).where(UserMemorySettingsModel.user_id == user_id)
        )

    def memory_enabled(self, user_id: str) -> bool:
        """Return whether memory may be retrieved or written for this user."""
        settings = self.get_settings(user_id)
        return bool(settings and settings.enabled)

    def get_or_create_settings(self, user_id: str) -> UserMemorySettingsModel:
        self._expire_user_pending_suggestions(user_id)
        settings = self.get_settings(user_id)
        if settings is not None:
            return settings
        settings = UserMemorySettingsModel(user_id=user_id, enabled=False)
        self._db.add(settings)
        self._db.commit()
        self._db.refresh(settings)
        return settings

    def set_enabled(self, user_id: str, enabled: bool) -> UserMemorySettingsModel:
        self._expire_user_pending_suggestions(user_id)
        settings = self.get_or_create_settings(user_id)
        settings.enabled = enabled
        settings.updated_at = datetime.now(UTC)
        self._db.commit()
        self._db.refresh(settings)
        return settings

    def store_explicit_memory(
        self,
        *,
        user_id: str,
        content: str,
        category: MemoryCategory | str,
        value: dict[str, Any] | None = None,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        source_run_id: str | None = None,
        source_document_id: str | None = None,
    ) -> UserMemoryModel:
        """Persist an explicit user-requested memory after policy validation."""
        self._expire_user_pending_suggestions(user_id)
        policy = evaluate_memory_write(
            content=_policy_text(content, value),
            category=category,
            mode=MemoryWriteMode.EXPLICIT,
            source_document_id=source_document_id,
        )
        if policy.decision != MemoryPolicyDecision.ALLOW:
            raise MemoryPolicyError(policy.reason)
        assert policy.category is not None
        assert policy.provenance_type is not None
        return self._store_memory_after_policy(
            user_id=user_id,
            content=content,
            category=policy.category,
            provenance_type=policy.provenance_type,
            sensitivity=policy.sensitivity,
            value=value,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            source_run_id=source_run_id,
            source_document_id=source_document_id,
        )

    def auto_store_memory(
        self,
        *,
        user_id: str,
        content: str,
        category: MemoryCategory | str,
        value: dict[str, Any] | None = None,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        source_run_id: str | None = None,
        source_document_id: str | None = None,
    ) -> UserMemoryModel | None:
        """Persist a bounded auto-memory, returning None when policy/opt-in rejects it."""
        self._expire_user_pending_suggestions(user_id)
        if not self.memory_enabled(user_id):
            return None
        policy = evaluate_memory_write(
            content=_policy_text(content, value),
            category=category,
            mode=MemoryWriteMode.AUTO_STORE,
            source_document_id=source_document_id,
        )
        if policy.decision != MemoryPolicyDecision.ALLOW:
            return None
        assert policy.category is not None
        assert policy.provenance_type is not None
        return self._store_memory_after_policy(
            user_id=user_id,
            content=content,
            category=policy.category,
            provenance_type=policy.provenance_type,
            sensitivity=policy.sensitivity,
            value=value,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            source_run_id=source_run_id,
            source_document_id=source_document_id,
        )

    def create_memory_suggestion(
        self,
        *,
        user_id: str,
        content: str,
        category: MemoryCategory | str,
        value: dict[str, Any] | None = None,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        source_run_id: str | None = None,
        source_document_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> MemorySuggestionModel:
        """Create a pending suggest-confirm record without activating memory."""
        self._expire_user_pending_suggestions(user_id)
        if not self.memory_enabled(user_id):
            raise MemoryDisabledError("long-term memory is disabled for this user")
        policy = evaluate_memory_write(
            content=_policy_text(content, value),
            category=category,
            mode=MemoryWriteMode.SUGGEST_CONFIRM,
            source_document_id=source_document_id,
        )
        if policy.decision != MemoryPolicyDecision.ALLOW:
            raise MemoryPolicyError(policy.reason)
        assert policy.category is not None
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("memory suggestion content must not be blank")
        value_document = _memory_value_document(
            content=normalized_content,
            category=policy.category.value,
            provenance_type=MemoryProvenanceType.ASSISTANT_SUGGESTED.value,
            sensitivity=policy.sensitivity.value,
            value=value,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            source_run_id=source_run_id,
            source_document_id=source_document_id,
        )
        suggestion = MemorySuggestionModel(
            user_id=user_id,
            category=policy.category.value,
            content=normalized_content,
            value_json=_json_dumps(value_document),
            status=MemorySuggestionStatus.PENDING.value,
            sensitivity=policy.sensitivity.value,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            source_run_id=source_run_id,
            source_document_id=source_document_id,
            expires_at=expires_at or datetime.now(UTC) + DEFAULT_SUGGESTION_TTL,
        )
        self._db.add(suggestion)
        self._db.commit()
        self._db.refresh(suggestion)
        return suggestion

    def list_memory_suggestions(
        self,
        *,
        user_id: str,
        include_decided: bool = False,
    ) -> list[MemorySuggestionModel]:
        self._expire_user_pending_suggestions(user_id)
        statuses = [MemorySuggestionStatus.PENDING.value]
        if include_decided:
            statuses.extend(
                [
                    MemorySuggestionStatus.CONFIRMED.value,
                    MemorySuggestionStatus.REJECTED.value,
                    MemorySuggestionStatus.EXPIRED.value,
                ]
            )
        return list(
            self._db.scalars(
                select(MemorySuggestionModel)
                .where(
                    MemorySuggestionModel.user_id == user_id,
                    MemorySuggestionModel.status.in_(statuses),
                )
                .order_by(MemorySuggestionModel.created_at.desc(), MemorySuggestionModel.id.desc())
            ).all()
        )

    def confirm_memory_suggestion(self, *, user_id: str, suggestion_id: str) -> UserMemoryModel:
        suggestion = self._pending_suggestion_for_user(user_id=user_id, suggestion_id=suggestion_id)
        now = datetime.now(UTC)
        if _datetime_lte(suggestion.expires_at, now):
            self._expire_suggestion(suggestion, now=now)
            self._db.commit()
            raise MemorySuggestionUnavailableError("memory suggestion expired")
        if not self.memory_enabled(user_id):
            raise MemoryDisabledError("long-term memory is disabled for this user")
        memory = self._store_memory_after_policy(
            user_id=user_id,
            content=suggestion.content,
            category=suggestion.category,
            provenance_type=MemoryProvenanceType.ASSISTANT_SUGGESTED,
            sensitivity=suggestion.sensitivity,
            value=memory_suggestion_value(suggestion),
            source_conversation_id=suggestion.source_conversation_id,
            source_message_id=suggestion.source_message_id,
            source_run_id=suggestion.source_run_id,
            source_document_id=suggestion.source_document_id,
            commit=False,
        )
        suggestion.status = MemorySuggestionStatus.CONFIRMED.value
        suggestion.decided_at = now
        suggestion.updated_at = now
        suggestion.memory_id = memory.id
        self._scrub_suggestion_content(suggestion, reason="confirmed")
        self._db.commit()
        self._db.refresh(memory)
        return memory

    def reject_memory_suggestion(
        self, *, user_id: str, suggestion_id: str
    ) -> MemorySuggestionModel:
        suggestion = self._pending_suggestion_for_user(user_id=user_id, suggestion_id=suggestion_id)
        now = datetime.now(UTC)
        if _datetime_lte(suggestion.expires_at, now):
            self._expire_suggestion(suggestion, now=now)
            self._db.commit()
            raise MemorySuggestionUnavailableError("memory suggestion expired")
        suggestion.status = MemorySuggestionStatus.REJECTED.value
        suggestion.decided_at = now
        suggestion.updated_at = now
        self._scrub_suggestion_content(suggestion, reason="rejected")
        self._db.commit()
        self._db.refresh(suggestion)
        return suggestion

    def expire_pending_suggestions(
        self, *, user_id: str | None = None, now: datetime | None = None
    ) -> int:
        current_time = now or datetime.now(UTC)
        statement = select(MemorySuggestionModel).where(
            MemorySuggestionModel.status == MemorySuggestionStatus.PENDING.value
        )
        if user_id is not None:
            statement = statement.where(MemorySuggestionModel.user_id == user_id)
        suggestions = self._db.scalars(statement).all()
        expired = 0
        for suggestion in suggestions:
            if _datetime_lte(suggestion.expires_at, current_time):
                self._expire_suggestion(suggestion, now=current_time)
                expired += 1
        if expired:
            self._db.commit()
        return expired

    def _store_memory_after_policy(
        self,
        *,
        user_id: str,
        content: str,
        category: MemoryCategory | str,
        provenance_type: MemoryProvenanceType | str,
        value: dict[str, Any] | None = None,
        sensitivity: MemorySensitivity | str = MemorySensitivity.NON_SENSITIVE,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        source_run_id: str | None = None,
        source_document_id: str | None = None,
        key: str | None = None,
        require_enabled: bool = True,
        commit: bool = True,
    ) -> UserMemoryModel:
        """Persist a policy-checked user-scoped memory JSON document."""
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("memory content must not be blank")
        category_value = _enum_value(MemoryCategory, category)
        provenance_value = _enum_value(MemoryProvenanceType, provenance_type)
        sensitivity_value = _enum_value(MemorySensitivity, sensitivity)
        if require_enabled and not self.memory_enabled(user_id):
            raise MemoryDisabledError("long-term memory is disabled for this user")

        memory_key = key or str(uuid.uuid4())
        namespace = user_memory_namespace(user_id, category_value)
        value_document = _memory_value_document(
            content=normalized_content,
            category=category_value,
            provenance_type=provenance_value,
            sensitivity=sensitivity_value,
            value=value,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            source_run_id=source_run_id,
            source_document_id=source_document_id,
        )
        memory = UserMemoryModel(
            user_id=user_id,
            namespace_json=_json_dumps(list(namespace)),
            key=memory_key,
            category=category_value,
            content=normalized_content,
            value_json=_json_dumps(value_document),
            status=MemoryStatus.ACTIVE.value,
            sensitivity=sensitivity_value,
            provenance_type=provenance_value,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            source_run_id=source_run_id,
            source_document_id=source_document_id,
        )
        self._db.add(memory)
        if commit:
            self._db.commit()
            self._db.refresh(memory)
        else:
            self._db.flush()
        return memory

    def list_memories(
        self,
        *,
        user_id: str,
        include_inactive: bool = True,
        include_deleted: bool = False,
    ) -> list[UserMemoryModel]:
        """List manageable memories for a user without crossing user boundaries."""
        self._expire_user_pending_suggestions(user_id)
        statuses = [MemoryStatus.ACTIVE.value]
        if include_inactive:
            statuses.append(MemoryStatus.INACTIVE.value)
        if include_deleted:
            statuses.append(MemoryStatus.DELETED.value)
        return list(
            self._db.scalars(
                select(UserMemoryModel)
                .where(UserMemoryModel.user_id == user_id, UserMemoryModel.status.in_(statuses))
                .order_by(UserMemoryModel.created_at.desc(), UserMemoryModel.id.desc())
            ).all()
        )

    def active_memories_for_context(
        self,
        *,
        user_id: str,
        categories: list[MemoryCategory | str] | None = None,
        query: str = "",
        limit: int = 8,
    ) -> list[UserMemoryModel]:
        """Return memories allowed to enter provider context.

        Disabled user memory returns an empty list while retaining manageable records.
        """
        self._expire_user_pending_suggestions(user_id)
        if not self.memory_enabled(user_id):
            return []
        statement = select(UserMemoryModel).where(
            UserMemoryModel.user_id == user_id,
            UserMemoryModel.status == MemoryStatus.ACTIVE.value,
            UserMemoryModel.sensitivity == MemorySensitivity.NON_SENSITIVE.value,
            UserMemoryModel.stale_at.is_(None),
        )
        if categories:
            statement = statement.where(
                UserMemoryModel.category.in_(
                    [_enum_value(MemoryCategory, item) for item in categories]
                )
            )
        candidates = list(
            self._db.scalars(
                statement.order_by(
                    UserMemoryModel.updated_at.desc(), UserMemoryModel.id.desc()
                ).limit(max(limit * 4, limit))
            ).all()
        )
        query_tokens = _meaningful_tokens(query)
        relevant = [memory for memory in candidates if _memory_relevant(memory, query_tokens)]
        return relevant[:limit]

    def deactivate_memory(self, *, user_id: str, memory_id: str) -> UserMemoryModel:
        self._expire_user_pending_suggestions(user_id)
        memory = self._memory_for_user(user_id=user_id, memory_id=memory_id)
        if memory.status == MemoryStatus.DELETED.value:
            raise MemoryNotFoundError("memory not found")
        now = datetime.now(UTC)
        memory.status = MemoryStatus.INACTIVE.value
        memory.deactivated_at = now
        memory.updated_at = now
        self._db.commit()
        self._db.refresh(memory)
        return memory

    def delete_memory(self, *, user_id: str, memory_id: str) -> None:
        self._expire_user_pending_suggestions(user_id)
        memory = self._memory_for_user(user_id=user_id, memory_id=memory_id)
        if memory.status == MemoryStatus.DELETED.value:
            raise MemoryNotFoundError("memory not found")
        now = datetime.now(UTC)
        memory.status = MemoryStatus.DELETED.value
        memory.content = ""
        memory.value_json = _json_dumps(
            {
                "deleted": True,
                "deleted_at": now.isoformat(),
                "category": memory.category,
                "provenance": {
                    "type": memory.provenance_type,
                    "source_conversation_id": memory.source_conversation_id,
                    "source_message_id": memory.source_message_id,
                    "source_run_id": memory.source_run_id,
                    "source_document_id": memory.source_document_id,
                },
            }
        )
        memory.deleted_at = now
        memory.updated_at = now
        for suggestion in self._db.scalars(
            select(MemorySuggestionModel).where(MemorySuggestionModel.memory_id == memory.id)
        ).all():
            self._scrub_suggestion_content(suggestion, reason="memory_deleted")
        self._db.commit()

    def mark_document_memories_stale(
        self,
        *,
        source_document_id: str,
        stale_reason: str = "source_document_deleted",
        commit: bool = True,
    ) -> int:
        """Exclude document-derived memories when their source document is invalidated."""
        now = datetime.now(UTC)
        memories = self._db.scalars(
            select(UserMemoryModel).where(
                UserMemoryModel.source_document_id == source_document_id,
                UserMemoryModel.status == MemoryStatus.ACTIVE.value,
                UserMemoryModel.stale_at.is_(None),
            )
        ).all()
        for memory in memories:
            memory.stale_at = now
            memory.stale_reason = stale_reason
            memory.updated_at = now
        if memories and commit:
            self._db.commit()
        elif memories:
            self._db.flush()
        return len(memories)

    def mark_transcript_memories_stale(
        self,
        *,
        source_conversation_id: str | None = None,
        source_message_ids: Iterable[str] = (),
        source_run_ids: Iterable[str] = (),
        stale_reason: str = "source_transcript_deleted",
        commit: bool = True,
    ) -> int:
        """Exclude memories whose conversation transcript source was pruned or deleted."""
        message_ids = _unique_non_empty(source_message_ids)
        run_ids = _unique_non_empty(source_run_ids)
        clauses = []
        if source_conversation_id:
            clauses.append(UserMemoryModel.source_conversation_id == source_conversation_id)
        if message_ids:
            clauses.append(UserMemoryModel.source_message_id.in_(message_ids))
        if run_ids:
            clauses.append(UserMemoryModel.source_run_id.in_(run_ids))
        if not clauses:
            return 0

        now = datetime.now(UTC)
        memories = self._db.scalars(
            select(UserMemoryModel).where(
                or_(*clauses),
                UserMemoryModel.status == MemoryStatus.ACTIVE.value,
                UserMemoryModel.stale_at.is_(None),
            )
        ).all()
        for memory in memories:
            memory.stale_at = now
            memory.stale_reason = stale_reason
            memory.updated_at = now
        if memories and commit:
            self._db.commit()
        elif memories:
            self._db.flush()
        return len(memories)

    def _memory_for_user(self, *, user_id: str, memory_id: str) -> UserMemoryModel:
        memory = self._db.get(UserMemoryModel, memory_id)
        if memory is None or memory.user_id != user_id:
            raise MemoryNotFoundError("memory not found")
        return memory

    def _pending_suggestion_for_user(
        self, *, user_id: str, suggestion_id: str
    ) -> MemorySuggestionModel:
        suggestion = self._db.get(MemorySuggestionModel, suggestion_id)
        if suggestion is None or suggestion.user_id != user_id:
            raise MemorySuggestionNotFoundError("memory suggestion not found")
        if suggestion.status != MemorySuggestionStatus.PENDING.value:
            raise MemorySuggestionUnavailableError("memory suggestion is not pending")
        return suggestion

    def _expire_suggestion(self, suggestion: MemorySuggestionModel, *, now: datetime) -> None:
        suggestion.status = MemorySuggestionStatus.EXPIRED.value
        suggestion.decided_at = now
        suggestion.updated_at = now
        self._scrub_suggestion_content(suggestion, reason="expired")

    def _scrub_suggestion_content(self, suggestion: MemorySuggestionModel, *, reason: str) -> None:
        suggestion.content = ""
        suggestion.value_json = _json_dumps(
            {
                "scrubbed": True,
                "scrubbed_reason": reason,
                "category": suggestion.category,
                "provenance": {
                    "type": MemoryProvenanceType.ASSISTANT_SUGGESTED.value,
                    "source_conversation_id": suggestion.source_conversation_id,
                    "source_message_id": suggestion.source_message_id,
                    "source_run_id": suggestion.source_run_id,
                    "source_document_id": suggestion.source_document_id,
                },
            }
        )


def user_memory_namespace(user_id: str, category: MemoryCategory | str) -> tuple[str, str, str]:
    """Return the LangGraph-store-style namespace for one user's memory category."""
    return (user_id, MEMORY_NAMESPACE_KIND, _enum_value(MemoryCategory, category))


def memory_value(memory: UserMemoryModel) -> dict[str, Any]:
    """Decode a stored memory value JSON document."""
    return json.loads(memory.value_json)


def memory_suggestion_value(suggestion: MemorySuggestionModel) -> dict[str, Any]:
    """Decode a pending memory suggestion value JSON document."""
    return json.loads(suggestion.value_json)


def memory_namespace(memory: UserMemoryModel) -> tuple[str, ...]:
    """Decode a stored memory namespace."""
    return tuple(json.loads(memory.namespace_json))


def _memory_value_document(
    *,
    content: str,
    category: str,
    provenance_type: str,
    sensitivity: str,
    value: dict[str, Any] | None,
    source_conversation_id: str | None,
    source_message_id: str | None,
    source_run_id: str | None,
    source_document_id: str | None,
) -> dict[str, Any]:
    value_document = dict(value or {})
    value_document.update(
        {
            "content": content,
            "category": category,
            "sensitivity": sensitivity,
            "provenance": {
                "type": provenance_type,
                "source_conversation_id": source_conversation_id,
                "source_message_id": source_message_id,
                "source_run_id": source_run_id,
                "source_document_id": source_document_id,
            },
        }
    )
    return value_document


def _policy_text(content: str, value: dict[str, Any] | None) -> str:
    if not value:
        return content
    return f"{content}\n{_json_dumps(value)}"


def _memory_relevant(memory: UserMemoryModel, query_tokens: set[str]) -> bool:
    if memory.category == MemoryCategory.STABLE_PREFERENCE.value:
        return has_preference_shape(memory.content)
    if not query_tokens:
        return False
    memory_tokens = _meaningful_tokens(f"{memory.category} {memory.content}")
    return bool(query_tokens & memory_tokens)


def _meaningful_tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w가-힣]{4,}", text.casefold()))


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unique_non_empty(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _enum_value(
    enum_type: type[MemoryCategory] | type[MemoryProvenanceType] | type[MemorySensitivity],
    value: Any,
) -> str:
    if isinstance(value, enum_type):
        return value.value
    return enum_type(str(value)).value


def _datetime_lte(left: datetime, right: datetime) -> bool:
    if left.tzinfo is None:
        left = left.replace(tzinfo=UTC)
    if right.tzinfo is None:
        right = right.replace(tzinfo=UTC)
    return left <= right
