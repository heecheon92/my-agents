"""Legacy whole-KB publication backfill tests."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from my_agents.knowledge.models import (
    DocumentModel,
    KnowledgeBaseModel,
    KnowledgeBasePublicationModel,
    KnowledgePublishRequestModel,
)
from my_agents.knowledge.publication_copies import backfill_legacy_publication_copies
from my_agents.persistence.database import get_database_session

from .test_publish_requests import (
    _client,
    _create_group,
    _create_personal_kb,
    _ingest,
    _invite_and_accept_member,
    _retrieval_hits,
    _signup_login,
    _upload_docx_document,
)


def test_legacy_publication_backfill_creates_group_copies_and_deletes_legacy_rows(
    monkeypatch,
) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    requester = _client(monkeypatch)
    viewer = _client(monkeypatch)
    owner_id = _signup_login(owner, "backfill-owner@example.com")
    requester_id = _signup_login(requester, "backfill-requester@example.com")
    viewer_id = _signup_login(viewer, "backfill-viewer@example.com")
    group_id = _create_group(owner, name="Backfill Group")
    _invite_and_accept_member(
        owner=owner,
        recipient=requester,
        group_id=group_id,
        recipient_email="backfill-requester@example.com",
        role="viewer",
    )
    _invite_and_accept_member(
        owner=owner,
        recipient=viewer,
        group_id=group_id,
        recipient_email="backfill-viewer@example.com",
        role="viewer",
    )
    related_source_kb_id = _create_personal_kb(requester, "Legacy Candidate")
    related_source_document_id = _upload_docx_document(
        requester,
        kb_id=related_source_kb_id,
        title="Legacy Candidate Document",
    )
    _ingest(requester, kb_id=related_source_kb_id, document_id=related_source_document_id)
    orphan_source_kb_id = _create_personal_kb(requester, "Legacy Candidate")
    orphan_source_document_id = _upload_docx_document(
        requester,
        kb_id=orphan_source_kb_id,
        title="Legacy Orphan Document",
    )
    _ingest(requester, kb_id=orphan_source_kb_id, document_id=orphan_source_document_id)

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        publish_request = KnowledgePublishRequestModel(
            requester_user_id=requester_id,
            target_group_id=group_id,
            target_knowledge_base_id=None,
            source_document_id=None,
            source_knowledge_base_id=related_source_kb_id,
            status="approved",
            reviewer_user_id=owner_id,
            published_knowledge_base_id=related_source_kb_id,
            reviewed_at=datetime.now(UTC),
        )
        db.add(publish_request)
        db.flush()
        db.add(
            KnowledgeBasePublicationModel(
                group_id=group_id,
                knowledge_base_id=related_source_kb_id,
                requester_user_id=requester_id,
                approved_by_user_id=owner_id,
                publish_request_id=publish_request.id,
            )
        )
        db.add(
            KnowledgeBasePublicationModel(
                group_id=group_id,
                knowledge_base_id=orphan_source_kb_id,
                requester_user_id=requester_id,
                approved_by_user_id=owner_id,
                publish_request_id=None,
            )
        )
        db.commit()
        request_id = publish_request.id

        assert (
            _retrieval_hits(
                viewer_id,
                kb_ids=[related_source_kb_id],
                query="GKPublishWordArtifact",
            )
            == []
        )
        dry_run = backfill_legacy_publication_copies(db, dry_run=True)
        assert dry_run.dry_run is True
        assert dry_run.scanned_publications == 2
        assert dry_run.created_group_copies == 0
        assert {plan.action for plan in dry_run.plans} == {"copy_personal_kb_to_group"}

        applied = backfill_legacy_publication_copies(db, dry_run=False)
        assert applied.dry_run is False
        assert applied.scanned_publications == 2
        assert applied.created_group_copies == 2
        assert applied.copied_documents == 2
        assert applied.updated_publish_requests == 1
        assert applied.deleted_publications == 2
        assert db.scalars(select(KnowledgeBasePublicationModel)).all() == []

        updated_request = db.get(KnowledgePublishRequestModel, request_id)
        assert updated_request is not None
        assert updated_request.published_knowledge_base_id != related_source_kb_id
        assert updated_request.source_knowledge_base_name_snapshot == "Legacy Candidate"
        assert updated_request.published_knowledge_base_name_snapshot == "Legacy Candidate"

        group_copies = db.scalars(
            select(KnowledgeBaseModel)
            .where(
                KnowledgeBaseModel.group_id == group_id,
                KnowledgeBaseModel.scope == "group",
                KnowledgeBaseModel.name.like("Legacy Candidate%"),
            )
            .order_by(KnowledgeBaseModel.name)
        ).all()
        assert [knowledge_base.name for knowledge_base in group_copies] == [
            "Legacy Candidate",
            "Legacy Candidate (published copy)",
        ]
        copied_documents = db.scalars(
            select(DocumentModel).where(DocumentModel.group_id == group_id)
        ).all()
        assert {document.title for document in copied_documents} == {
            "Legacy Candidate Document",
            "Legacy Orphan Document",
        }
        assert (
            _retrieval_hits(
                viewer_id,
                kb_ids=[related_source_kb_id],
                query="GKPublishWordArtifact",
            )
            == []
        )
        assert _retrieval_hits(
            viewer_id,
            kb_ids=[updated_request.published_knowledge_base_id],
            query="GKPublishWordArtifact",
        )

        requester_knowledge_bases = requester.get("/knowledge-bases").json()
        original_listing = next(
            item for item in requester_knowledge_bases if item["id"] == related_source_kb_id
        )
        copied_listing = next(
            item
            for item in requester_knowledge_bases
            if item["id"] == updated_request.published_knowledge_base_id
        )
        assert original_listing["published_group_ids"] == []
        assert copied_listing["published_group_ids"] == [group_id]

        second_apply = backfill_legacy_publication_copies(db, dry_run=False)
        assert second_apply.scanned_publications == 0
        assert second_apply.created_group_copies == 0
        assert len(db.scalars(select(KnowledgePublishRequestModel)).all()) == 1
    finally:
        session_generator.close()
