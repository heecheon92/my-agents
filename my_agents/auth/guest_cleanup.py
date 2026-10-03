"""Expired guest erasure, retaining only content-free audit and keyed email history."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from threading import Event

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from my_agents.auth.models import (
    AuthTokenModel,
    GuestAccessCodeModel,
    GuestAccessRequestModel,
    GuestDeletionAuditModel,
    SessionModel,
    UserModel,
)
from my_agents.conversations.continuity_models import ConversationSummaryModel
from my_agents.conversations.models import (
    AgentEventModel,
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from my_agents.document_workspace.models import (
    ConversationArtifactModel,
    ConversationAttachmentModel,
    DocumentWorkspaceModel,
    UsageEventModel,
)
from my_agents.groups.models import GroupInvitationModel, GroupModel, MembershipModel
from my_agents.knowledge.models import (
    CitationModel,
    DocumentChunkModel,
    DocumentModel,
    DocumentPermissionModel,
    EntityMentionModel,
    EntityModel,
    EntityRelationshipModel,
    ExtractionRunModel,
    KnowledgeBaseModel,
    KnowledgeBasePublicationModel,
    KnowledgePublishRequestModel,
)
from my_agents.knowledge.publication_copies import delete_document_artifacts
from my_agents.memory.models import MemorySuggestionModel, UserMemoryModel, UserMemorySettingsModel
from my_agents.memory.store_projection import delete_user_memory_projection
from my_agents.persistence.langgraph import LangGraphPersistenceResources
from my_agents.settings import Settings

logger = logging.getLogger(__name__)


def _exists(db: Session, model: type, *conditions: object) -> bool:
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) > 0


def _defer_reason(
    db: Session,
    user_id: str,
    kb_ids: list[str],
    doc_ids: list[str],
    run_ids: list[str],
    conv_ids: list[str],
) -> str | None:
    if _exists(
        db,
        AgentRunModel,
        AgentRunModel.user_id == user_id,
        AgentRunModel.status.in_(["running", "cancelling"]),
    ):
        return "active_run"
    if _exists(
        db,
        ExtractionRunModel,
        ExtractionRunModel.document_id.in_(doc_ids),
        ExtractionRunModel.status.in_(["pending", "queued", "running"]),
    ):
        return "active_ingestion"
    # Guests cannot normally use provider workspaces. Defer legacy/inconsistent rows
    # rather than claim erasure without deleting their provider-side copies.
    if _exists(
        db,
        ConversationAttachmentModel,
        or_(
            ConversationAttachmentModel.owner_user_id == user_id,
            ConversationAttachmentModel.conversation_id.in_(conv_ids),
        ),
    ) or _exists(
        db,
        DocumentWorkspaceModel,
        or_(
            DocumentWorkspaceModel.owner_user_id == user_id,
            DocumentWorkspaceModel.conversation_id.in_(conv_ids),
        ),
    ):
        return "provider_workspace_requires_review"
    if _exists(
        db,
        ConversationArtifactModel,
        or_(
            ConversationArtifactModel.owner_user_id == user_id,
            ConversationArtifactModel.conversation_id.in_(conv_ids),
        ),
    ):
        return "provider_workspace_requires_review"
    if _exists(db, GroupModel, GroupModel.created_by_user_id == user_id):
        return "shared_ownership"
    if _exists(
        db,
        KnowledgeBaseModel,
        KnowledgeBaseModel.owner_user_id == user_id,
        or_(
            KnowledgeBaseModel.scope != "personal",
            KnowledgeBaseModel.group_id.is_not(None),
            KnowledgeBaseModel.purpose != "standard",
        ),
    ):
        return "shared_ownership"
    if _exists(
        db,
        DocumentModel,
        DocumentModel.owner_user_id == user_id,
        or_(DocumentModel.group_id.is_not(None), ~DocumentModel.knowledge_base_id.in_(kb_ids)),
    ):
        return "shared_ownership"
    if _exists(
        db,
        DocumentModel,
        DocumentModel.knowledge_base_id.in_(kb_ids),
        DocumentModel.owner_user_id != user_id,
    ):
        return "shared_ownership"
    if _exists(
        db,
        DocumentPermissionModel,
        DocumentPermissionModel.document_id.in_(doc_ids),
        DocumentPermissionModel.user_id != user_id,
    ):
        return "shared_document"
    if _exists(
        db,
        KnowledgeBasePublicationModel,
        KnowledgeBasePublicationModel.knowledge_base_id.in_(kb_ids),
    ):
        return "shared_knowledge_base"
    if _exists(
        db,
        CitationModel,
        CitationModel.document_id.in_(doc_ids),
        ~CitationModel.run_id.in_(run_ids),
    ):
        return "shared_document_evidence"
    if _exists(
        db,
        KnowledgePublishRequestModel,
        KnowledgePublishRequestModel.requester_user_id != user_id,
        or_(
            KnowledgePublishRequestModel.source_document_id.in_(doc_ids),
            KnowledgePublishRequestModel.source_knowledge_base_id.in_(kb_ids),
        ),
    ):
        return "shared_publication_request"
    return None


def delete_expired_guest(
    db: Session,
    *,
    user_id: str,
    cutoff: datetime,
    email_hmac_key: str,
    resources: LangGraphPersistenceResources,
    dry_run: bool = False,
) -> str:
    """One account per transaction; external failures leave it expired and retryable."""
    eligible = (
        UserModel.id == user_id,
        UserModel.account_type == "guest",
        UserModel.guest_expires_at <= cutoff,
    )
    try:
        if not dry_run:
            # Acquire a write lock on SQLite and a row lock on PostgreSQL before
            # rechecking ownership/work. Competing cleanup workers recheck after the lock.
            claimed = db.execute(
                update(UserModel)
                .where(*eligible)
                .values(guest_expires_at=UserModel.guest_expires_at)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                db.rollback()
                return "not_eligible"
        user = db.scalar(
            select(UserModel).where(*eligible).execution_options(populate_existing=True)
        )
        if user is None:
            db.rollback()
            return "not_eligible"
        kb_ids = list(
            db.scalars(
                select(KnowledgeBaseModel.id).where(KnowledgeBaseModel.owner_user_id == user_id)
            )
        )
        doc_ids = list(
            db.scalars(select(DocumentModel.id).where(DocumentModel.owner_user_id == user_id))
        )
        conv_ids = list(
            db.scalars(
                select(ConversationModel.id).where(ConversationModel.owner_user_id == user_id)
            )
        )
        runs = db.scalars(
            select(AgentRunModel).where(
                or_(AgentRunModel.user_id == user_id, AgentRunModel.conversation_id.in_(conv_ids))
            )
        ).all()
        if any(run.user_id != user_id or run.conversation_id not in conv_ids for run in runs):
            db.rollback()
            return "inconsistent_run_ownership"
        run_ids = [run.id for run in runs]
        reason = _defer_reason(db, user_id, kb_ids, doc_ids, run_ids, conv_ids)
        if reason:
            db.rollback()
            return reason
        requests = db.scalars(
            select(GuestAccessRequestModel)
            .join(
                GuestAccessCodeModel, GuestAccessCodeModel.request_id == GuestAccessRequestModel.id
            )
            .where(GuestAccessCodeModel.guest_user_id == user_id)
        ).all()
        emails = {item.email.strip().casefold() for item in requests}
        if len(emails) > 1:
            db.rollback()
            return "inconsistent_request_emails"
        if dry_run:
            db.rollback()
            return "eligible"
        if len(email_hmac_key.encode()) < 32:
            raise ValueError("guest cleanup HMAC key must contain at least 32 bytes")
        # Use framework APIs before dropping Product DB references; failures roll back
        # the database and retry the idempotent framework deletions on a later sweep.
        if resources.checkpointer is not None:
            for run_id in run_ids:
                resources.checkpointer.delete_thread(run_id)
        if resources.store is not None:
            delete_user_memory_projection(resources.store, user_id=user_id)
        counts = {
            "knowledge_bases": len(kb_ids),
            "documents": len(doc_ids),
            "conversations": len(conv_ids),
            "runs": len(run_ids),
            "messages": db.scalar(
                select(func.count())
                .select_from(MessageModel)
                .where(MessageModel.conversation_id.in_(conv_ids))
            ),
            "chunks": db.scalar(
                select(func.count())
                .select_from(DocumentChunkModel)
                .where(DocumentChunkModel.document_id.in_(doc_ids))
            ),
        }
        candidate_entities = set(
            db.scalars(
                select(EntityMentionModel.entity_id).where(
                    EntityMentionModel.document_id.in_(doc_ids)
                )
            )
        )
        for row in db.execute(
            select(
                EntityRelationshipModel.source_entity_id, EntityRelationshipModel.target_entity_id
            ).where(EntityRelationshipModel.document_id.in_(doc_ids))
        ):
            candidate_entities.update(row)
        # Remove publication request snapshots, retaining separately owned published copies.
        publication_ids = select(KnowledgePublishRequestModel.id).where(
            KnowledgePublishRequestModel.requester_user_id == user_id
        )
        db.execute(
            update(KnowledgeBasePublicationModel)
            .where(KnowledgeBasePublicationModel.publish_request_id.in_(publication_ids))
            .values(publish_request_id=None)
        )
        db.execute(
            delete(KnowledgePublishRequestModel).where(
                KnowledgePublishRequestModel.requester_user_id == user_id
            )
        )
        db.execute(
            delete(DocumentPermissionModel).where(
                or_(
                    DocumentPermissionModel.user_id == user_id,
                    DocumentPermissionModel.document_id.in_(doc_ids),
                )
            )
        )
        for doc_id in doc_ids:
            delete_document_artifacts(db, document_id=doc_id)
        db.execute(delete(DocumentModel).where(DocumentModel.id.in_(doc_ids)))
        db.execute(delete(KnowledgeBaseModel).where(KnowledgeBaseModel.id.in_(kb_ids)))
        db.execute(
            delete(EntityModel).where(
                EntityModel.id.in_(candidate_entities),
                ~EntityModel.id.in_(select(EntityMentionModel.entity_id)),
                ~EntityModel.id.in_(select(EntityRelationshipModel.source_entity_id)),
                ~EntityModel.id.in_(select(EntityRelationshipModel.target_entity_id)),
            )
        )
        db.execute(delete(CitationModel).where(CitationModel.run_id.in_(run_ids)))
        db.execute(delete(AgentEventModel).where(AgentEventModel.run_id.in_(run_ids)))
        db.execute(
            delete(ConversationSummaryModel).where(
                ConversationSummaryModel.conversation_id.in_(conv_ids)
            )
        )
        db.execute(delete(AgentRunModel).where(AgentRunModel.id.in_(run_ids)))
        db.execute(delete(MessageModel).where(MessageModel.conversation_id.in_(conv_ids)))
        db.execute(delete(ConversationModel).where(ConversationModel.id.in_(conv_ids)))
        for model in [
            MemorySuggestionModel,
            UserMemoryModel,
            UserMemorySettingsModel,
            UsageEventModel,
            MembershipModel,
            SessionModel,
            AuthTokenModel,
        ]:
            db.execute(delete(model).where(model.user_id == user_id))
        db.execute(
            update(GroupInvitationModel)
            .where(GroupInvitationModel.accepted_by_user_id == user_id)
            .values(accepted_by_user_id=None)
        )
        db.execute(
            delete(GuestAccessCodeModel).where(GuestAccessCodeModel.guest_user_id == user_id)
        )
        request_ids = [item.id for item in requests]
        db.execute(
            delete(GuestAccessRequestModel).where(
                GuestAccessRequestModel.id.in_(request_ids),
                ~GuestAccessRequestModel.id.in_(
                    select(GuestAccessCodeModel.request_id).where(
                        GuestAccessCodeModel.request_id.is_not(None)
                    )
                ),
            )
        )
        fingerprint = (
            hmac.new(
                email_hmac_key.encode(), next(iter(emails)).encode(), hashlib.sha256
            ).hexdigest()
            if emails
            else None
        )
        db.add(
            GuestDeletionAuditModel(
                guest_user_id=user_id,
                email_fingerprint=fingerprint,
                guest_created_at=user.created_at,
                guest_expired_at=user.guest_expires_at,
                deleted_at=datetime.now(UTC),
                counts_json=json.dumps(counts, sort_keys=True),
                reason="guest_expired",
            )
        )
        db.execute(delete(UserModel).where(UserModel.id == user_id))
        db.commit()
        return "deleted"
    except Exception:
        db.rollback()
        raise


def cleanup_batch(
    db: Session,
    settings: Settings,
    resources: LangGraphPersistenceResources,
    *,
    after_id: str = "",
    stop: Event | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, int], str]:
    """Scan a keyset page so deferred accounts cannot starve later eligible guests."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.guest_cleanup_grace_seconds)
    ids = list(
        db.scalars(
            select(UserModel.id)
            .where(
                UserModel.account_type == "guest",
                UserModel.guest_expires_at <= cutoff,
                UserModel.id > after_id,
            )
            .order_by(UserModel.id)
            .limit(25)
        )
    )
    db.rollback()
    counts: Counter[str] = Counter()
    cursor = ""
    for user_id in ids:
        if stop is not None and stop.is_set():
            break
        cursor = user_id
        try:
            result = delete_expired_guest(
                db,
                user_id=user_id,
                cutoff=cutoff,
                email_hmac_key=settings.guest_cleanup_email_hmac_key.get_secret_value()
                if settings.guest_cleanup_email_hmac_key
                else "",
                resources=resources,
                dry_run=dry_run,
            )
            counts[result] += 1
        except Exception as exc:
            counts["failed"] += 1
            logger.warning("guest_cleanup.failed error_class=%s", type(exc).__name__)
    return dict(counts), cursor


async def guest_cleanup_loop(settings: Settings, resources: LangGraphPersistenceResources) -> None:
    from my_agents.persistence.database import (
        _sessionmaker_for_url,
        initialize_database,
        supports_background_sessions,
    )

    if not settings.guest_cleanup_enabled or not supports_background_sessions(
        settings.database_url
    ):
        return
    stop = Event()
    cursor = ""
    while True:

        def sweep(after_id: str = cursor) -> tuple[dict[str, int], str]:
            initialize_database(settings)
            with _sessionmaker_for_url(settings.database_url)() as db:
                return cleanup_batch(db, settings, resources, after_id=after_id, stop=stop)

        task = asyncio.create_task(asyncio.to_thread(sweep))
        try:
            counts, cursor = await asyncio.shield(task)
            if counts:
                logger.info("guest_cleanup.batch counts=%s", counts)
        except asyncio.CancelledError:
            stop.set()
            try:
                await task
            except Exception as exc:
                logger.warning("guest_cleanup.shutdown_failed error_class=%s", type(exc).__name__)
            raise
        except Exception as exc:
            logger.warning("guest_cleanup.sweep_failed error_class=%s", type(exc).__name__)
        await asyncio.sleep(60)
