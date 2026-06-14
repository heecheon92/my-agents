"""Long-term memory service contract tests."""

from __future__ import annotations

import pytest

from my_agents.memory.models import MemoryCategory, MemoryProvenanceType, MemoryStatus
from my_agents.memory.service import (
    MemoryDisabledError,
    MemoryPolicyError,
    MemorySuggestionUnavailableError,
    UserMemoryService,
    memory_namespace,
    memory_value,
)
from my_agents.persistence.database import get_database_session


def test_memory_service_defaults_disabled_and_blocks_context_injection(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        settings = service.get_or_create_settings("user-a")

        assert settings.enabled is False
        assert service.active_memories_for_context(user_id="user-a") == []
        with pytest.raises(MemoryDisabledError):
            service._store_memory_after_policy(
                user_id="user-a",
                content="Prefers concise answers",
                category=MemoryCategory.STABLE_PREFERENCE,
                provenance_type=MemoryProvenanceType.EXPLICIT_USER,
            )
    finally:
        session_generator.close()


def test_memory_service_retains_records_but_hides_them_when_disabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("user-a", True)
        memory = service._store_memory_after_policy(
            user_id="user-a",
            content="Project Phoenix uses FastAPI",
            category=MemoryCategory.PROJECT_CONTEXT,
            provenance_type=MemoryProvenanceType.EXPLICIT_USER,
            source_conversation_id="conversation-1",
            source_message_id="message-1",
        )

        assert memory_namespace(memory) == ("user-a", "memories", "project_context")
        assert memory_value(memory)["provenance"]["source_message_id"] == "message-1"
        assert service.active_memories_for_context(
            user_id="user-a", query="Project Phoenix FastAPI"
        ) == [memory]

        service.set_enabled("user-a", False)

        assert service.active_memories_for_context(user_id="user-a") == []
        assert service.list_memories(user_id="user-a") == [memory]
    finally:
        session_generator.close()


def test_memory_service_soft_delete_and_user_isolation(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("owner", True)
        memory = service._store_memory_after_policy(
            user_id="owner",
            content="Owner likes Korean explanations",
            category=MemoryCategory.STABLE_PREFERENCE,
            provenance_type=MemoryProvenanceType.EXPLICIT_USER,
        )

        assert service.list_memories(user_id="other") == []
        service.delete_memory(user_id="owner", memory_id=memory.id)

        assert service.list_memories(user_id="owner") == []
        deleted = service.list_memories(user_id="owner", include_deleted=True)[0]
        assert deleted.status == MemoryStatus.DELETED.value
        assert deleted.deleted_at is not None
    finally:
        session_generator.close()


def test_memory_policy_rejects_sensitive_auto_and_explicit_writes(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("user-sensitive", True)

        assert (
            service.auto_store_memory(
                user_id="user-sensitive",
                content="My password is swordfish",
                category=MemoryCategory.PERSONAL_FACT,
            )
            is None
        )
        with pytest.raises(MemoryPolicyError):
            service.store_explicit_memory(
                user_id="user-sensitive",
                content="My password is swordfish",
                category=MemoryCategory.PERSONAL_FACT,
            )
    finally:
        session_generator.close()


def test_stable_preference_requires_preference_shape_for_write_and_recall(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("preference-user", True)

        with pytest.raises(MemoryPolicyError):
            service.store_explicit_memory(
                user_id="preference-user",
                content="Project Phoenix uses FastAPI",
                category=MemoryCategory.STABLE_PREFERENCE,
            )

        bypassed_legacy_row = service._store_memory_after_policy(
            user_id="preference-user",
            content="Project Phoenix uses FastAPI",
            category=MemoryCategory.STABLE_PREFERENCE,
            provenance_type=MemoryProvenanceType.EXPLICIT_USER,
        )
        valid = service.store_explicit_memory(
            user_id="preference-user",
            content="User prefers concise answers",
            category=MemoryCategory.STABLE_PREFERENCE,
        )

        recalled = service.active_memories_for_context(
            user_id="preference-user", query="What should I cook tonight?"
        )

        assert valid in recalled
        assert bypassed_legacy_row not in recalled
    finally:
        session_generator.close()


def test_memory_suggestion_confirm_reject_and_expiry_lifecycle(monkeypatch) -> None:  # noqa: ANN001
    from datetime import UTC, datetime, timedelta

    from my_agents.memory.models import MemorySuggestionStatus

    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("suggest-user", True)
        suggestion = service.create_memory_suggestion(
            user_id="suggest-user",
            content="User prefers short answers",
            category=MemoryCategory.STABLE_PREFERENCE,
            source_conversation_id="conversation-1",
        )

        assert suggestion.status == MemorySuggestionStatus.PENDING.value
        assert service.active_memories_for_context(user_id="suggest-user") == []

        memory = service.confirm_memory_suggestion(
            user_id="suggest-user", suggestion_id=suggestion.id
        )
        assert memory.provenance_type == MemoryProvenanceType.ASSISTANT_SUGGESTED.value
        assert service.active_memories_for_context(user_id="suggest-user") == [memory]
        db.refresh(suggestion)
        assert suggestion.content == ""
        assert "User prefers short answers" not in suggestion.value_json

        rejected = service.create_memory_suggestion(
            user_id="suggest-user",
            content="User uses Korean docs",
            category=MemoryCategory.PROJECT_CONTEXT,
        )
        service.reject_memory_suggestion(user_id="suggest-user", suggestion_id=rejected.id)
        db.refresh(rejected)
        assert rejected.content == ""
        assert "User uses Korean docs" not in rejected.value_json
        assert service.list_memory_suggestions(user_id="suggest-user") == []

        expired = service.create_memory_suggestion(
            user_id="suggest-user",
            content="User uses pytest",
            category=MemoryCategory.PROJECT_CONTEXT,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        with pytest.raises(MemorySuggestionUnavailableError):
            service.confirm_memory_suggestion(user_id="suggest-user", suggestion_id=expired.id)
    finally:
        session_generator.close()


def test_memory_suggestion_expiry_is_user_scoped_and_reject_expires_first(monkeypatch) -> None:  # noqa: ANN001
    from datetime import UTC, datetime, timedelta

    from my_agents.memory.models import MemorySuggestionStatus

    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("list-user", True)
        service.set_enabled("other-user", True)
        expired_for_list_user = service.create_memory_suggestion(
            user_id="list-user",
            content="User uses pytest",
            category=MemoryCategory.PROJECT_CONTEXT,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        expired_for_other_user = service.create_memory_suggestion(
            user_id="other-user",
            content="User uses FastAPI",
            category=MemoryCategory.PROJECT_CONTEXT,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        assert service.list_memory_suggestions(user_id="list-user") == []
        db.refresh(expired_for_list_user)
        db.refresh(expired_for_other_user)
        assert expired_for_list_user.status == MemorySuggestionStatus.EXPIRED.value
        assert expired_for_other_user.status == MemorySuggestionStatus.PENDING.value

        with pytest.raises(MemorySuggestionUnavailableError):
            service.reject_memory_suggestion(
                user_id="other-user", suggestion_id=expired_for_other_user.id
            )
        db.refresh(expired_for_other_user)
        assert expired_for_other_user.status == MemorySuggestionStatus.EXPIRED.value
    finally:
        session_generator.close()


def test_document_derived_memory_requires_source_and_can_be_marked_stale(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("doc-user", True)

        assert (
            service.auto_store_memory(
                user_id="doc-user",
                content="The project launch date is July 1",
                category=MemoryCategory.DOCUMENT_DERIVED_FACT,
            )
            is None
        )
        memory = service.auto_store_memory(
            user_id="doc-user",
            content="The project launch date is July 1",
            category=MemoryCategory.DOCUMENT_DERIVED_FACT,
            source_document_id="document-1",
        )
        assert memory is not None
        assert memory.provenance_type == MemoryProvenanceType.DOCUMENT_DERIVED.value
        assert service.active_memories_for_context(
            user_id="doc-user", query="project launch date"
        ) == [memory]

        assert service.mark_document_memories_stale(source_document_id="document-1") == 1
        assert service.active_memories_for_context(user_id="doc-user") == []
        stale = service.list_memories(user_id="doc-user")[0]
        assert stale.stale_reason == "source_document_deleted"
    finally:
        session_generator.close()


def test_deleted_memory_scrubs_content_and_value(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("scrub-user", True)
        memory = service._store_memory_after_policy(
            user_id="scrub-user",
            content="Erase this private detail",
            category=MemoryCategory.PERSONAL_FACT,
            provenance_type=MemoryProvenanceType.EXPLICIT_USER,
            value={"private": "Erase this private detail"},
        )

        service.delete_memory(user_id="scrub-user", memory_id=memory.id)

        deleted = service.list_memories(user_id="scrub-user", include_deleted=True)[0]
        assert deleted.content == ""
        assert "Erase this private detail" not in deleted.value_json
        assert memory_value(deleted)["deleted"] is True
    finally:
        session_generator.close()


def test_delete_memory_scrubs_confirmed_suggestion_duplicate_content(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("suggest-delete-user", True)
        suggestion = service.create_memory_suggestion(
            user_id="suggest-delete-user",
            content="User prefers Socratic explanations",
            category=MemoryCategory.STABLE_PREFERENCE,
        )
        memory = service.confirm_memory_suggestion(
            user_id="suggest-delete-user", suggestion_id=suggestion.id
        )

        service.delete_memory(user_id="suggest-delete-user", memory_id=memory.id)
        db.refresh(suggestion)

        assert suggestion.content == ""
        assert "User prefers Socratic explanations" not in suggestion.value_json
    finally:
        session_generator.close()


def test_context_recall_minimizes_non_preference_memory_by_query(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("recall-user", True)
        stable = service._store_memory_after_policy(
            user_id="recall-user",
            content="User prefers concise answers",
            category=MemoryCategory.STABLE_PREFERENCE,
            provenance_type=MemoryProvenanceType.EXPLICIT_USER,
        )
        project = service._store_memory_after_policy(
            user_id="recall-user",
            content="Project Phoenix uses FastAPI",
            category=MemoryCategory.PROJECT_CONTEXT,
            provenance_type=MemoryProvenanceType.EXPLICIT_USER,
        )

        unrelated = service.active_memories_for_context(
            user_id="recall-user", query="What should I cook tonight?"
        )
        related = service.active_memories_for_context(
            user_id="recall-user", query="Help with Project Phoenix FastAPI routing"
        )

        assert unrelated == [stable]
        assert related == [project, stable]
    finally:
        session_generator.close()


def test_expired_suggestions_are_scrubbed_during_context_recall(monkeypatch) -> None:  # noqa: ANN001
    from datetime import UTC, datetime, timedelta

    from my_agents.memory.models import MemorySuggestionStatus

    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("expiry-recall-user", True)
        suggestion = service.create_memory_suggestion(
            user_id="expiry-recall-user",
            content="User uses private legacy test stack",
            category=MemoryCategory.PROJECT_CONTEXT,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        assert (
            service.active_memories_for_context(
                user_id="expiry-recall-user", query="legacy test stack"
            )
            == []
        )
        db.refresh(suggestion)
        assert suggestion.status == MemorySuggestionStatus.EXPIRED.value
        assert suggestion.content == ""
        assert "private legacy test stack" not in suggestion.value_json
    finally:
        session_generator.close()


def test_pruned_transcript_memories_are_staled_without_staling_preserved_prefix(
    monkeypatch,
) -> None:  # noqa: ANN001
    from my_agents.api.conversations.transcripts import prune_conversation_from_message
    from my_agents.conversations.models import (
        AgentRunModel,
        ConversationModel,
        MessageModel,
        MessageRole,
        RunStatus,
    )

    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("replay-user", True)
        conversation = ConversationModel(owner_user_id="replay-user", title="Replay prune")
        db.add(conversation)
        db.flush()
        first_user_message = MessageModel(
            conversation_id=conversation.id,
            role=MessageRole.USER.value,
            content="I prefer concise answers.",
        )
        assistant_message = MessageModel(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT.value,
            content="Noted.",
        )
        later_user_message = MessageModel(
            conversation_id=conversation.id,
            role=MessageRole.USER.value,
            content="I also prefer Socratic answers.",
        )
        db.add_all([first_user_message, assistant_message, later_user_message])
        db.flush()
        run = AgentRunModel(
            conversation_id=conversation.id,
            user_id="replay-user",
            status=RunStatus.COMPLETED.value,
            assistant_message_id=assistant_message.id,
        )
        db.add(run)
        db.commit()

        preserved = service._store_memory_after_policy(
            user_id="replay-user",
            content="User prefers concise answers",
            category=MemoryCategory.STABLE_PREFERENCE,
            provenance_type=MemoryProvenanceType.EXPLICIT_USER,
            source_conversation_id=conversation.id,
            source_message_id=first_user_message.id,
        )
        pruned = service._store_memory_after_policy(
            user_id="replay-user",
            content="User prefers Socratic answers",
            category=MemoryCategory.STABLE_PREFERENCE,
            provenance_type=MemoryProvenanceType.ASSISTANT_SUGGESTED,
            source_conversation_id=conversation.id,
            source_message_id=assistant_message.id,
            source_run_id=run.id,
        )

        prune_conversation_from_message(
            db,
            conversation_id=conversation.id,
            target_message=assistant_message,
            removed_messages=[assistant_message, later_user_message],
            original_run=run,
        )

        db.refresh(preserved)
        db.refresh(pruned)
        assert preserved.stale_at is None
        assert pruned.stale_reason == "source_transcript_pruned"
        assert service.active_memories_for_context(user_id="replay-user") == [preserved]
    finally:
        session_generator.close()


def test_deleted_conversation_stales_conversation_sourced_memories(monkeypatch) -> None:  # noqa: ANN001
    from my_agents.api.conversations.transcripts import delete_conversation_tree
    from my_agents.conversations.models import ConversationModel

    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("delete-conversation-user", True)
        conversation = ConversationModel(
            owner_user_id="delete-conversation-user", title="Delete memory sources"
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        memory = service._store_memory_after_policy(
            user_id="delete-conversation-user",
            content="User prefers Korean answers",
            category=MemoryCategory.STABLE_PREFERENCE,
            provenance_type=MemoryProvenanceType.EXPLICIT_USER,
            source_conversation_id=conversation.id,
        )

        delete_conversation_tree(db, conversation)

        db.refresh(memory)
        assert memory.stale_reason == "source_conversation_deleted"
        assert service.active_memories_for_context(user_id="delete-conversation-user") == []
    finally:
        session_generator.close()
