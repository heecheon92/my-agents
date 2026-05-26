"""Add email-gated guest access requests."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260526_0016"
down_revision: str | Sequence[str] | None = "20260525_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guest_access_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guest_access_requests")),
    )
    op.create_index(
        op.f("ix_guest_access_requests_email"),
        "guest_access_requests",
        ["email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_guest_access_requests_status"),
        "guest_access_requests",
        ["status"],
        unique=False,
    )
    context = op.get_context()
    if context.as_sql:
        _add_request_reference_without_fk()
    elif context.dialect.name == "sqlite":
        with op.batch_alter_table(
            "guest_access_codes",
            copy_from=_guest_access_codes_table(),
        ) as batch_op:
            batch_op.add_column(sa.Column("request_id", sa.String(length=36), nullable=True))
            batch_op.create_index(
                op.f("ix_guest_access_codes_code_hash"),
                ["code_hash"],
                unique=False,
            )
            batch_op.create_index(
                op.f("ix_guest_access_codes_guest_user_id"),
                ["guest_user_id"],
                unique=False,
            )
            batch_op.create_foreign_key(
                op.f("fk_guest_access_codes_request_id_guest_access_requests"),
                "guest_access_requests",
                ["request_id"],
                ["id"],
            )
            batch_op.create_index(
                op.f("ix_guest_access_codes_request_id"),
                ["request_id"],
                unique=False,
            )
    else:
        _add_request_reference_without_fk()
        op.create_foreign_key(
            op.f("fk_guest_access_codes_request_id_guest_access_requests"),
            "guest_access_codes",
            "guest_access_requests",
            ["request_id"],
            ["id"],
        )


def downgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        _drop_request_reference_without_fk()
    elif context.dialect.name == "sqlite":
        with op.batch_alter_table(
            "guest_access_codes",
            copy_from=_guest_access_codes_table(include_request_id=True),
        ) as batch_op:
            batch_op.drop_index(op.f("ix_guest_access_codes_request_id"))
            batch_op.drop_index(op.f("ix_guest_access_codes_guest_user_id"))
            batch_op.drop_index(op.f("ix_guest_access_codes_code_hash"))
            batch_op.drop_constraint(
                op.f("fk_guest_access_codes_request_id_guest_access_requests"),
                type_="foreignkey",
            )
            batch_op.drop_column("request_id")
    else:
        op.drop_index(op.f("ix_guest_access_codes_request_id"), table_name="guest_access_codes")
        op.drop_constraint(
            op.f("fk_guest_access_codes_request_id_guest_access_requests"),
            "guest_access_codes",
            type_="foreignkey",
        )
        op.drop_column("guest_access_codes", "request_id")
    op.drop_index(op.f("ix_guest_access_requests_status"), table_name="guest_access_requests")
    op.drop_index(op.f("ix_guest_access_requests_email"), table_name="guest_access_requests")
    op.drop_table("guest_access_requests")


def _add_request_reference_without_fk() -> None:
    op.add_column(
        "guest_access_codes",
        sa.Column("request_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        op.f("ix_guest_access_codes_request_id"),
        "guest_access_codes",
        ["request_id"],
        unique=False,
    )


def _drop_request_reference_without_fk() -> None:
    op.drop_index(op.f("ix_guest_access_codes_request_id"), table_name="guest_access_codes")
    op.drop_column("guest_access_codes", "request_id")


def _guest_access_codes_table(*, include_request_id: bool = False) -> sa.Table:
    columns = [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("guest_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["guest_user_id"],
            ["users.id"],
            name=op.f("fk_guest_access_codes_guest_user_id_users"),
        ),
        sa.UniqueConstraint("code_hash", name="uq_guest_access_codes_code_hash"),
    ]
    if include_request_id:
        columns.append(
            sa.Column(
                "request_id",
                sa.String(length=36),
                sa.ForeignKey("guest_access_requests.id"),
                nullable=True,
            )
        )
    return sa.Table("guest_access_codes", sa.MetaData(), *columns)
