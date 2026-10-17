"""Persist independent answer jobs and resumable display output."""

import sqlalchemy as sa

from alembic import context, op

revision = "20261017_0040"
down_revision = "20261003_0039"
branch_labels = None
depends_on = None


def _active_index(include_queued: bool) -> None:
    statuses = "'running', 'waiting_for_input', 'cancelling'"
    if include_queued:
        statuses = "'queued', " + statuses
    condition = sa.text(f"status IN ({statuses})")
    op.create_index(
        "uq_agent_runs_active_conversation",
        "agent_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=condition,
        sqlite_where=condition,
    )


def upgrade() -> None:
    op.create_table(
        "durable_run_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("client_request_id", sa.String(36), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "kind", "client_request_id"),
    )
    op.create_index("ix_durable_run_jobs_run_id", "durable_run_jobs", ["run_id"])
    op.create_index("ix_durable_run_jobs_status", "durable_run_jobs", ["status"])
    op.create_table(
        "durable_run_outputs",
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("min_cursor", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("terminal_event", sa.String(80), nullable=True),
        sa.Column("terminal_data_json", sa.Text(), nullable=True),
        sa.Column("terminal_cursor", sa.Integer(), nullable=True),
    )
    op.create_table(
        "durable_run_frames",
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cursor", sa.Integer(), primary_key=True),
        sa.Column("event", sa.String(80), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "durable_worker_heartbeats",
        sa.Column("worker_id", sa.String(80), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.drop_index("uq_agent_runs_active_conversation", table_name="agent_runs")
    _active_index(include_queued=True)


def downgrade() -> None:
    # Never discard accepted work or weaken active-run uniqueness while a durable
    # operation can still execute. Stop admission/workers and drain/cancel first.
    pending_sql = (
        "SELECT 1 FROM agent_runs WHERE status = 'queued' "
        "UNION ALL SELECT 1 FROM durable_run_jobs WHERE status IN ('queued', 'running') "
        "UNION ALL SELECT 1 FROM agent_runs r JOIN durable_run_outputs o ON o.run_id = r.id "
        "WHERE r.status IN ('running', 'waiting_for_input', 'cancelling')"
    )
    if context.is_offline_mode():
        if op.get_bind().dialect.name != "postgresql":
            raise RuntimeError("Durable-run downgrade requires an online pending-work check")
        op.execute(
            "DO $$ BEGIN IF EXISTS ("
            + pending_sql
            + ") THEN RAISE EXCEPTION 'Drain durable jobs before downgrade'; END IF; END $$"
        )
    elif op.get_bind().execute(sa.text(pending_sql)).first() is not None:
        raise RuntimeError("Drain durable jobs before downgrade")
    op.drop_index("uq_agent_runs_active_conversation", table_name="agent_runs")
    _active_index(include_queued=False)
    op.drop_table("durable_worker_heartbeats")
    op.drop_table("durable_run_frames")
    op.drop_table("durable_run_outputs")
    op.drop_table("durable_run_jobs")
