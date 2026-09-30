"""Conversation-derived summaries, file notes and cleanup targets."""

import sqlalchemy as sa

from alembic import op

revision = "20260930_0036"
down_revision = "20260930_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("summarization_model_preference", sa.String(80), nullable=True)
    )
    op.add_column("agent_runs", sa.Column("summarization_model", sa.String(80), nullable=True))
    op.add_column("agent_runs", sa.Column("user_message_id", sa.String(36), nullable=True))
    op.create_index("ix_agent_runs_user_message_id", "agent_runs", ["user_message_id"])
    op.add_column(
        "conversation_attachments", sa.Column("content_sha256", sa.String(64), nullable=True)
    )
    op.add_column(
        "conversation_attachments",
        sa.Column("cleanup_scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, type_ in (
        ("context_delivery_json", sa.Text()),
        ("attachment_notes_json", sa.Text()),
        ("requested_assistant_model", sa.String(80)),
        ("requested_workspace_model", sa.String(80)),
        ("client_request_id", sa.String(36)),
        ("requested_reasoning_mode", sa.String(20)),
        ("requested_reasoning_effort", sa.String(20)),
    ):
        op.add_column("agent_runs", sa.Column(name, type_, nullable=True))
    op.create_index(
        "uq_agent_runs_client_request", "agent_runs", ["user_id", "client_request_id"], unique=True
    )
    op.create_table(
        "conversation_summaries",
        sa.Column(
            "conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), primary_key=True
        ),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column("covered_message_id", sa.String(36), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("body_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "message_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column(
            "attachment_id",
            sa.String(36),
            sa.ForeignKey("conversation_attachments.id"),
            nullable=False,
        ),
        sa.UniqueConstraint("message_id", "attachment_id", name="uq_message_attachment_pair"),
    )
    op.create_table(
        "attachment_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "attachment_id",
            sa.String(36),
            sa.ForeignKey("conversation_attachments.id"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("body_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("run_id", "attachment_id", name="uq_attachment_note_run"),
    )
    op.create_table(
        "provider_cleanup",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    for table, columns in (
        ("message_attachments", ("message_id", "attachment_id")),
        ("attachment_notes", ("attachment_id", "run_id")),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in (
        "attachment_notes",
        "message_attachments",
        "conversation_summaries",
        "provider_cleanup",
    ):
        op.drop_table(table)
    op.drop_index("uq_agent_runs_client_request", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_message_id", table_name="agent_runs")
    op.drop_column("agent_runs", "user_message_id")
    op.drop_column("agent_runs", "summarization_model")
    op.drop_column("users", "summarization_model_preference")
    op.drop_column("conversation_attachments", "content_sha256")
    op.drop_column("conversation_attachments", "cleanup_scheduled_at")
    for name in (
        "context_delivery_json",
        "attachment_notes_json",
        "requested_assistant_model",
        "requested_workspace_model",
        "client_request_id",
        "requested_reasoning_mode",
        "requested_reasoning_effort",
    ):
        op.drop_column("agent_runs", name)
