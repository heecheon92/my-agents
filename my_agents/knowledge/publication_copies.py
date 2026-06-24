"""Helpers for cutting legacy personal-KB publications over to group-owned copies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.models import (
    CitationModel,
    DocumentChunkModel,
    DocumentMetadataProfileModel,
    DocumentModel,
    DocumentParseArtifactModel,
    EntityMentionModel,
    EntityRelationshipModel,
    ExtractionRunModel,
    KnowledgeBaseModel,
    KnowledgeBasePublicationModel,
    KnowledgeBasePurpose,
    KnowledgeBaseScope,
    KnowledgePublishRequestModel,
    KnowledgePublishRequestStatus,
    StructuredKnowledgeEntityModel,
)


@dataclass(frozen=True)
class KnowledgeBaseCopyResult:
    """A newly copied group knowledge base and its copied document IDs."""

    knowledge_base: KnowledgeBaseModel
    document_ids: tuple[str, ...]


@dataclass(frozen=True)
class PublicationBackfillPlan:
    """One legacy publication row planned or applied by the backfill."""

    publication_id: str
    source_knowledge_base_id: str
    target_group_id: str
    publish_request_id: str | None
    action: str


@dataclass(frozen=True)
class PublicationBackfillSummary:
    """Counters returned by the legacy publication backfill."""

    dry_run: bool
    scanned_publications: int = 0
    created_group_copies: int = 0
    copied_documents: int = 0
    updated_publish_requests: int = 0
    deleted_publications: int = 0
    skipped_publications: int = 0
    plans: tuple[PublicationBackfillPlan, ...] = field(default_factory=tuple)


def copy_personal_knowledge_base_to_group(
    db: Session,
    *,
    source_knowledge_base: KnowledgeBaseModel,
    target_group_id: str,
    group_owner_user_id: str,
    copy_name: str | None = None,
) -> KnowledgeBaseCopyResult:
    """Create a group-owned KB copy and ingest copied documents.

    ``KnowledgeExtractionService.ingest_document`` commits progress internally, so
    rollback must explicitly delete every copied document and the group KB on failure.
    """

    _require_personal_standard_source(source_knowledge_base)
    group_copy = KnowledgeBaseModel(
        name=_unique_group_knowledge_base_name(
            db,
            group_id=target_group_id,
            preferred_name=copy_name or source_knowledge_base.name,
        ),
        scope=KnowledgeBaseScope.GROUP.value,
        owner_user_id=group_owner_user_id,
        group_id=target_group_id,
        purpose=KnowledgeBasePurpose.STANDARD.value,
    )
    copied_document_ids: list[str] = []
    db.add(group_copy)
    db.flush()

    source_documents = db.scalars(
        select(DocumentModel)
        .where(
            DocumentModel.knowledge_base_id == source_knowledge_base.id,
            DocumentModel.owner_user_id == source_knowledge_base.owner_user_id,
            DocumentModel.group_id.is_(None),
        )
        .order_by(DocumentModel.created_at.asc(), DocumentModel.id.asc())
    ).all()
    try:
        for source_document in source_documents:
            copied_document = _copy_document_for_group_knowledge_base(
                source_document=source_document,
                target_knowledge_base=group_copy,
                owner_user_id=group_owner_user_id,
            )
            db.add(copied_document)
            db.flush()
            copied_document_ids.append(copied_document.id)
            _copy_parse_artifacts_for_document(
                db,
                source_document=source_document,
                copied_document=copied_document,
            )
            KnowledgeExtractionService(db).ingest_document(copied_document)
        return KnowledgeBaseCopyResult(
            knowledge_base=group_copy,
            document_ids=tuple(copied_document_ids),
        )
    except Exception:
        db.rollback()
        delete_knowledge_base_copy(db, knowledge_base_id=group_copy.id)
        db.commit()
        raise


def backfill_legacy_publication_copies(
    db: Session,
    *,
    dry_run: bool = True,
) -> PublicationBackfillSummary:
    """Backfill legacy personal-KB publication rows into group-owned KB copies."""

    publications = db.scalars(
        select(KnowledgeBasePublicationModel).order_by(
            KnowledgeBasePublicationModel.created_at,
            KnowledgeBasePublicationModel.id,
        )
    ).all()
    plans: list[PublicationBackfillPlan] = []
    created_group_copies = 0
    copied_documents = 0
    updated_publish_requests = 0
    deleted_publications = 0
    skipped_publications = 0

    for publication in publications:
        source_knowledge_base = db.get(KnowledgeBaseModel, publication.knowledge_base_id)
        publish_request = (
            db.get(KnowledgePublishRequestModel, publication.publish_request_id)
            if publication.publish_request_id is not None
            else None
        )
        if source_knowledge_base is None or not _is_personal_standard_source(source_knowledge_base):
            skipped_publications += 1
            plans.append(
                PublicationBackfillPlan(
                    publication_id=publication.id,
                    source_knowledge_base_id=publication.knowledge_base_id,
                    target_group_id=publication.group_id,
                    publish_request_id=publication.publish_request_id,
                    action="skip_non_personal_source",
                )
            )
            continue

        plans.append(
            PublicationBackfillPlan(
                publication_id=publication.id,
                source_knowledge_base_id=source_knowledge_base.id,
                target_group_id=publication.group_id,
                publish_request_id=publication.publish_request_id,
                action="copy_personal_kb_to_group",
            )
        )
        if dry_run:
            continue

        result = copy_personal_knowledge_base_to_group(
            db,
            source_knowledge_base=source_knowledge_base,
            target_group_id=publication.group_id,
            group_owner_user_id=publication.approved_by_user_id,
        )
        created_group_copies += 1
        copied_documents += len(result.document_ids)

        if publish_request is not None:
            _point_publish_request_at_group_copy(
                publish_request=publish_request,
                source_knowledge_base=source_knowledge_base,
                copied_knowledge_base=result.knowledge_base,
                reviewer_user_id=publication.approved_by_user_id,
                reviewed_at=publication.created_at,
            )
            db.add(publish_request)
            updated_publish_requests += 1

        db.delete(publication)
        db.commit()
        deleted_publications += 1

    return PublicationBackfillSummary(
        dry_run=dry_run,
        scanned_publications=len(publications),
        created_group_copies=created_group_copies,
        copied_documents=copied_documents,
        updated_publish_requests=updated_publish_requests,
        deleted_publications=deleted_publications,
        skipped_publications=skipped_publications,
        plans=tuple(plans),
    )


def delete_knowledge_base_copy(db: Session, *, knowledge_base_id: str) -> None:
    """Delete a copied KB and all dependent document artifacts."""

    documents = db.scalars(
        select(DocumentModel).where(DocumentModel.knowledge_base_id == knowledge_base_id)
    ).all()
    for document in documents:
        delete_document_artifacts(db, document_id=document.id)
        db.delete(document)
    knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
    if knowledge_base is not None:
        db.delete(knowledge_base)
    db.flush()


def delete_document_artifacts(db: Session, *, document_id: str) -> None:
    """Remove rows that hold direct or indirect references to a document."""

    db.execute(delete(CitationModel).where(CitationModel.document_id == document_id))
    db.execute(
        delete(StructuredKnowledgeEntityModel).where(
            StructuredKnowledgeEntityModel.document_id == document_id
        )
    )
    db.execute(
        delete(DocumentMetadataProfileModel).where(
            DocumentMetadataProfileModel.document_id == document_id
        )
    )
    db.execute(
        delete(EntityRelationshipModel).where(EntityRelationshipModel.document_id == document_id)
    )
    db.execute(delete(EntityMentionModel).where(EntityMentionModel.document_id == document_id))
    db.execute(delete(DocumentChunkModel).where(DocumentChunkModel.document_id == document_id))
    db.execute(delete(ExtractionRunModel).where(ExtractionRunModel.document_id == document_id))
    db.execute(
        delete(DocumentParseArtifactModel).where(
            DocumentParseArtifactModel.document_id == document_id
        )
    )


def _point_publish_request_at_group_copy(
    *,
    publish_request: KnowledgePublishRequestModel,
    source_knowledge_base: KnowledgeBaseModel,
    copied_knowledge_base: KnowledgeBaseModel,
    reviewer_user_id: str,
    reviewed_at: datetime,
) -> None:
    publish_request.status = KnowledgePublishRequestStatus.APPROVED.value
    publish_request.reviewer_user_id = publish_request.reviewer_user_id or reviewer_user_id
    publish_request.reviewed_at = publish_request.reviewed_at or reviewed_at
    publish_request.published_knowledge_base_id = copied_knowledge_base.id
    publish_request.source_knowledge_base_name_snapshot = (
        publish_request.source_knowledge_base_name_snapshot or source_knowledge_base.name
    )
    publish_request.published_knowledge_base_name_snapshot = copied_knowledge_base.name


def _copy_document_for_group_knowledge_base(
    *,
    source_document: DocumentModel,
    target_knowledge_base: KnowledgeBaseModel,
    owner_user_id: str,
) -> DocumentModel:
    return DocumentModel(
        title=source_document.title,
        content=source_document.content,
        source_type=source_document.source_type,
        source_filename=source_document.source_filename,
        source_content_type=source_document.source_content_type,
        source_byte_size=source_document.source_byte_size,
        source_sha256=source_document.source_sha256,
        source_page_count=source_document.source_page_count,
        parser_name=source_document.parser_name,
        owner_user_id=owner_user_id,
        group_id=target_knowledge_base.group_id,
        knowledge_base_id=target_knowledge_base.id,
    )


def _copy_parse_artifacts_for_document(
    db: Session,
    *,
    source_document: DocumentModel,
    copied_document: DocumentModel,
) -> None:
    artifacts = db.scalars(
        select(DocumentParseArtifactModel).where(
            DocumentParseArtifactModel.document_id == source_document.id
        )
    ).all()
    for artifact in artifacts:
        db.add(
            DocumentParseArtifactModel(
                document_id=copied_document.id,
                source_sha256=artifact.source_sha256,
                source_filename=artifact.source_filename,
                source_content_type=artifact.source_content_type,
                source_type=artifact.source_type,
                parser_provider=artifact.parser_provider,
                parser_name=artifact.parser_name,
                parser_version=artifact.parser_version,
                parser_mode=artifact.parser_mode,
                markdown_content=artifact.markdown_content,
                elements_json=artifact.elements_json,
                warnings_json=artifact.warnings_json,
            )
        )


def _unique_group_knowledge_base_name(
    db: Session,
    *,
    group_id: str,
    preferred_name: str,
) -> str:
    base_name = preferred_name.strip() or "Published Knowledge"
    existing_names = set(
        db.scalars(
            select(KnowledgeBaseModel.name).where(
                KnowledgeBaseModel.scope == KnowledgeBaseScope.GROUP.value,
                KnowledgeBaseModel.group_id == group_id,
            )
        ).all()
    )
    if base_name not in existing_names:
        return base_name
    first_candidate = f"{base_name} (published copy)"
    if first_candidate not in existing_names:
        return first_candidate
    suffix = 2
    while True:
        candidate = f"{base_name} (published copy {suffix})"
        if candidate not in existing_names:
            return candidate
        suffix += 1


def _require_personal_standard_source(source_knowledge_base: KnowledgeBaseModel) -> None:
    if not _is_personal_standard_source(source_knowledge_base):
        raise ValueError("source knowledge base must be personal standard")


def _is_personal_standard_source(source_knowledge_base: KnowledgeBaseModel) -> bool:
    return (
        source_knowledge_base.scope == KnowledgeBaseScope.PERSONAL.value
        and source_knowledge_base.group_id is None
        and source_knowledge_base.purpose == KnowledgeBasePurpose.STANDARD.value
    )
