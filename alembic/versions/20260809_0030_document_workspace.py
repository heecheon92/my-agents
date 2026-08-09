"""Add ephemeral document workspaces, artifacts, and normalized usage events.

Revision ID: 20260809_0030
Revises: 20260624_0029
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260809_0030"
down_revision = "20260624_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_attachments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_file_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_attachments_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_attachments")),
        sa.UniqueConstraint(
            "provider_file_id", name=op.f("uq_conversation_attachments_provider_file_id")
        ),
    )
    for column in ("conversation_id", "owner_user_id", "extension", "status"):
        op.create_index(
            op.f(f"ix_conversation_attachments_{column}"), "conversation_attachments", [column]
        )

    op.create_table(
        "document_workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_container_id", sa.String(length=255), nullable=True),
        sa.Column("mounted_attachment_ids_json", sa.Text(), nullable=False),
        sa.Column("spreadsheet_skill_enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_document_workspaces_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_workspaces")),
        sa.UniqueConstraint("conversation_id", name="uq_document_workspaces_conversation_id"),
        sa.UniqueConstraint(
            "provider_container_id", name=op.f("uq_document_workspaces_provider_container_id")
        ),
    )
    for column in ("conversation_id", "owner_user_id", "status"):
        op.create_index(op.f(f"ix_document_workspaces_{column}"), "document_workspaces", [column])

    op.create_table(
        "agent_run_attachments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attachment_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], name=op.f("fk_agent_run_attachments_run_id_agent_runs")
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["conversation_attachments.id"],
            name=op.f("fk_agent_run_attachments_attachment_id_conversation_attachments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run_attachments")),
        sa.UniqueConstraint("run_id", "attachment_id", name="uq_agent_run_attachments_pair"),
    )
    op.create_index(op.f("ix_agent_run_attachments_run_id"), "agent_run_attachments", ["run_id"])
    op.create_index(
        op.f("ix_agent_run_attachments_attachment_id"), "agent_run_attachments", ["attachment_id"]
    )

    op.create_table(
        "conversation_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("provider_file_id", sa.String(length=255), nullable=False),
        sa.Column("provider_path", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["document_workspaces.id"],
            name=op.f("fk_conversation_artifacts_workspace_id_document_workspaces"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], name=op.f("fk_conversation_artifacts_run_id_agent_runs")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_artifacts_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_artifacts")),
    )
    for column in ("workspace_id", "run_id", "conversation_id", "owner_user_id", "status"):
        op.create_index(
            op.f(f"ix_conversation_artifacts_{column}"), "conversation_artifacts", [column]
        )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("capability", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("units_json", sa.Text(), nullable=False),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
        sa.UniqueConstraint("idempotency_key", name="uq_usage_events_idempotency_key"),
    )
    for column in ("user_id", "conversation_id", "run_id", "capability", "provider", "occurred_at"):
        op.create_index(op.f(f"ix_usage_events_{column}"), "usage_events", [column])


def downgrade() -> None:
    op.drop_table("usage_events")
    op.drop_table("conversation_artifacts")
    op.drop_table("agent_run_attachments")
    op.drop_table("document_workspaces")
    op.drop_table("conversation_attachments")
