"""Retain private document originals and pin ingestion and retrieval preferences."""

import sqlalchemy as sa

from alembic import context, op

revision = "20261018_0042"
down_revision = "20261017_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bucket", "object_key"),
        sa.CheckConstraint("byte_size >= 0", name="stored_file_byte_size_nonnegative"),
    )
    op.create_index("ix_stored_files_owner_user_id", "stored_files", ["owner_user_id"])
    op.create_index("ix_stored_files_status", "stored_files", ["status"])
    op.create_table(
        "storage_quota",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("id = 1", name="storage_quota_singleton"),
        sa.CheckConstraint("used_bytes >= 0", name="storage_quota_nonnegative"),
    )
    op.create_table(
        "storage_cleanup",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("stored_file_id", sa.String(36), sa.ForeignKey("stored_files.id"), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bucket", "object_key"),
    )
    op.create_index("ix_storage_cleanup_not_before", "storage_cleanup", ["not_before"])
    op.create_table(
        "document_upload_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(36), nullable=False),
        sa.Column(
            "stored_file_id", sa.String(36), sa.ForeignKey("stored_files.id"), nullable=False
        ),
        sa.Column(
            "staging_file_id", sa.String(36), sa.ForeignKey("stored_files.id"), nullable=True
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("ingestion_mode", sa.String(20), nullable=False),
        sa.Column("staging_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=True),
        sa.Column("extraction_run_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signed_until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_document_upload_sessions_owner_user_id", "document_upload_sessions", ["owner_user_id"]
    )
    if op.get_bind().dialect.name == "sqlite":
        # Nullable inline REFERENCES works online and in emitted SQL, without copying
        # existing documents or importing the current (possibly future) ORM schema.
        op.execute(
            "ALTER TABLE documents ADD COLUMN original_file_id VARCHAR(36) "
            "CONSTRAINT fk_documents_original_file_id_stored_files REFERENCES stored_files (id)"
        )
    else:
        op.add_column(
            "documents",
            sa.Column(
                "original_file_id",
                sa.String(36),
                sa.ForeignKey("stored_files.id", name="fk_documents_original_file_id_stored_files"),
                nullable=True,
            ),
        )
    op.create_index("ix_documents_original_file_id", "documents", ["original_file_id"])
    op.add_column(
        "documents",
        sa.Column("ingestion_mode", sa.String(20), server_default="standard", nullable=False),
    )
    op.add_column("documents", sa.Column("processing_provenance_json", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "document_parse_artifacts",
        sa.Column("processing_provenance_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("ingestion_mode", sa.String(20), server_default="standard", nullable=False),
    )
    op.add_column("extraction_runs", sa.Column("candidate_content", sa.Text(), nullable=True))
    op.add_column("extraction_runs", sa.Column("candidate_elements_json", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "document_retrieval_priority", sa.String(20), server_default="automatic", nullable=False
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "document_ingestion_mode", sa.String(20), server_default="standard", nullable=False
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "document_retrieval_priority", sa.String(20), server_default="automatic", nullable=False
        ),
    )
    # An old parsed document is not an original file. Do not invent an object link,
    # processing timestamp, or model provenance for existing rows.


def downgrade() -> None:
    pending_sql = (
        "SELECT 1 FROM stored_files "
        "UNION ALL SELECT 1 FROM storage_cleanup "
        "UNION ALL SELECT 1 FROM document_upload_sessions "
        "UNION ALL SELECT 1 FROM storage_quota WHERE used_bytes <> 0"
    )
    if context.is_offline_mode():
        if op.get_bind().dialect.name != "postgresql":
            raise RuntimeError("Private-file downgrade requires an online object-cleanup check")
        op.execute(
            "DO $$ BEGIN IF EXISTS ("
            + pending_sql
            + ") THEN RAISE EXCEPTION 'Remove retained originals and drain object cleanup "
            "before downgrade'; END IF; END $$"
        )
    elif op.get_bind().execute(sa.text(pending_sql)).first() is not None:
        raise RuntimeError("Remove retained originals and drain object cleanup before downgrade")
    op.drop_column("agent_runs", "document_retrieval_priority")
    op.drop_column("users", "document_ingestion_mode")
    op.drop_column("users", "document_retrieval_priority")
    op.drop_column("extraction_runs", "candidate_elements_json")
    op.drop_column("extraction_runs", "candidate_content")
    op.drop_column("extraction_runs", "ingestion_mode")
    op.drop_column("document_parse_artifacts", "processing_provenance_json")
    op.drop_column("documents", "processed_at")
    op.drop_column("documents", "processing_provenance_json")
    op.drop_column("documents", "ingestion_mode")
    op.drop_index("ix_documents_original_file_id", table_name="documents")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "fk_documents_original_file_id_stored_files", "documents", type_="foreignkey"
        )
    op.drop_column("documents", "original_file_id")
    op.drop_table("document_upload_sessions")
    op.drop_table("storage_cleanup")
    op.drop_table("storage_quota")
    op.drop_table("stored_files")
