"""Add display nickname to users."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "20260614_0026"
down_revision: str | Sequence[str] | None = "20260610_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_nickname(email: str | None, account_type: str | None) -> str:
    if account_type == "guest":
        return "Guest"
    local_part = (email or "").split("@", 1)[0].strip()
    return (local_part or "User")[:40]


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("nickname", sa.String(length=40), nullable=False, server_default="User"),
    )

    if context.is_offline_mode():
        op.execute(
            "UPDATE users "
            "SET nickname = CASE "
            "WHEN account_type = 'guest' THEN 'Guest' "
            "ELSE substr(CASE "
            "WHEN instr(email, '@') > 0 THEN substr(email, 1, instr(email, '@') - 1) "
            "ELSE 'User' END, 1, 40) END "
            "WHERE nickname IS NULL"
        )
    else:
        bind = op.get_bind()
        users = sa.table(
            "users",
            sa.column("id", sa.String(length=36)),
            sa.column("email", sa.String(length=320)),
            sa.column("account_type", sa.String(length=20)),
            sa.column("nickname", sa.String(length=40)),
        )
        for row in bind.execute(sa.select(users.c.id, users.c.email, users.c.account_type)):
            bind.execute(
                users.update()
                .where(users.c.id == row.id)
                .values(nickname=_backfill_nickname(row.email, row.account_type))
            )

    bind = None if context.is_offline_mode() else op.get_bind()
    if bind is not None and bind.dialect.name != "sqlite":
        op.alter_column(
            "users",
            "nickname",
            existing_type=sa.String(length=40),
            server_default=None,
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("nickname")
