"""Durable best-effort provider erasure with immediate application access revocation."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from my_agents.conversations.continuity_models import (
    AttachmentNoteModel,
    MessageAttachmentModel,
    ProviderCleanupModel,
)
from my_agents.document_workspace.models import (
    AgentRunAttachmentModel,
    ConversationArtifactModel,
    ConversationAttachmentModel,
    DocumentWorkspaceModel,
)

logger = logging.getLogger(__name__)


def enqueue(db: Session, kind: str, resource_id: str) -> None:
    if not db.scalar(
        select(ProviderCleanupModel.id).where(
            ProviderCleanupModel.resource_type == kind,
            ProviderCleanupModel.resource_id == resource_id,
        )
    ):
        db.add(
            ProviderCleanupModel(
                resource_type=kind, resource_id=resource_id, available_at=datetime.now(UTC)
            )
        )


def revoke_attachment(
    db: Session, attachment: ConversationAttachmentModel, *, explicit: bool
) -> None:
    from my_agents.conversations.continuity import invalidate_summary

    enqueue(db, "file", attachment.provider_file_id)
    attachment.status = "deleted" if explicit else "expired"
    attachment.cleanup_scheduled_at = datetime.now(UTC)
    workspace = db.scalar(
        select(DocumentWorkspaceModel).where(
            DocumentWorkspaceModel.conversation_id == attachment.conversation_id
        )
    )
    if workspace and workspace.provider_container_id:
        mounted = json.loads(workspace.mounted_attachment_ids_json)
        if isinstance(mounted, dict) and attachment.id in mounted:
            enqueue(
                db,
                "container_file",
                json.dumps([workspace.provider_container_id, mounted.pop(attachment.id)]),
            )
            workspace.mounted_attachment_ids_json = json.dumps(mounted)
        elif isinstance(mounted, list) and attachment.id in mounted:
            # Old containers have no reliable original-to-copy mapping. Retire once.
            enqueue(db, "container", workspace.provider_container_id)
            workspace.status = "expired"
            db.execute(
                update(ConversationArtifactModel)
                .where(ConversationArtifactModel.workspace_id == workspace.id)
                .values(status="expired")
            )
    if explicit:
        db.execute(
            delete(AttachmentNoteModel).where(AttachmentNoteModel.attachment_id == attachment.id)
        )
        invalidate_summary(db, attachment.conversation_id)
    if explicit or attachment.status == "expired":
        run_ids = select(AgentRunAttachmentModel.run_id).where(
            AgentRunAttachmentModel.attachment_id == attachment.id
        )
        artifacts = db.scalars(
            select(ConversationArtifactModel).where(ConversationArtifactModel.run_id.in_(run_ids))
        ).all()
        for artifact in artifacts:
            workspace_row = db.get(DocumentWorkspaceModel, artifact.workspace_id)
            if workspace_row and workspace_row.provider_container_id:
                enqueue(
                    db,
                    "container_file",
                    json.dumps([workspace_row.provider_container_id, artifact.provider_file_id]),
                )
            artifact.status = "deleted"


def cleanup_once(db: Session, provider: object) -> None:
    cleanup_provider = getattr(provider, "cleanup_provider", None)
    if callable(cleanup_provider):
        provider = cleanup_provider()
    now = datetime.now(UTC)
    submitted = select(MessageAttachmentModel.attachment_id)
    unused_before = now - timedelta(days=1)
    attachments = db.scalars(
        select(ConversationAttachmentModel)
        .where(
            ConversationAttachmentModel.cleanup_scheduled_at.is_(None),
            or_(
                ConversationAttachmentModel.status.in_(("expired", "deleted")),
                ConversationAttachmentModel.provider_expires_at <= now,
                (ConversationAttachmentModel.created_at <= unused_before)
                & ~ConversationAttachmentModel.id.in_(submitted),
            ),
        )
        .limit(25)
    ).all()
    for attachment in attachments:
        revoke_attachment(db, attachment, explicit=attachment.status == "deleted")
    db.commit()
    now = datetime.now(UTC)
    jobs = db.scalars(
        select(ProviderCleanupModel)
        .where(
            ProviderCleanupModel.available_at <= now,
            or_(ProviderCleanupModel.lease_until.is_(None), ProviderCleanupModel.lease_until < now),
        )
        .limit(25)
    ).all()
    for job in jobs:
        claimed = db.execute(
            update(ProviderCleanupModel)
            .where(
                ProviderCleanupModel.id == job.id,
                or_(
                    ProviderCleanupModel.lease_until.is_(None),
                    ProviderCleanupModel.lease_until < now,
                ),
            )
            .values(lease_until=now + timedelta(minutes=2))
        )
        db.commit()
        if claimed.rowcount != 1:
            continue
        try:
            if job.resource_type == "file":
                provider.delete_file(job.resource_id)
            elif job.resource_type == "container":
                provider.delete_container(job.resource_id)
            else:
                container_id, file_id = json.loads(job.resource_id)
                provider.delete_container_file(container_id=container_id, provider_file_id=file_id)
        except Exception as exc:
            if getattr(exc.__cause__, "status_code", None) != 404:
                job.available_at = now + timedelta(minutes=1)
                job.lease_until = None
                db.commit()
                logger.warning(
                    "document_cleanup.retry resource_type=%s error_class=%s",
                    job.resource_type,
                    type(exc).__name__,
                )
                continue
        db.delete(job)
        db.commit()


async def cleanup_loop(settings):  # noqa: ANN001, ANN201
    from my_agents.document_workspace.provider import OpenAIDocumentWorkspaceProvider
    from my_agents.persistence.database import _sessionmaker_for_url

    while True:
        await asyncio.sleep(60)

        def sweep() -> None:
            provider = OpenAIDocumentWorkspaceProvider(
                settings.model_copy(update={"document_workspace_timeout_seconds": 20})
            )
            with _sessionmaker_for_url(settings.database_url)() as db:
                cleanup_once(db, provider)

        try:
            await asyncio.to_thread(sweep)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("document_cleanup.sweep_failed error_class=%s", type(exc).__name__)
