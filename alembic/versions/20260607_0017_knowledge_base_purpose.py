"""Add knowledge-base purpose for hidden team-upload staging."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260607_0017"
down_revision: str | Sequence[str] | None = "20260526_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column("purpose", sa.String(length=40), nullable=False, server_default="standard"),
    )
    op.create_index(op.f("ix_knowledge_bases_purpose"), "knowledge_bases", ["purpose"])


def downgrade() -> None:
    op.drop_index(op.f("ix_knowledge_bases_purpose"), table_name="knowledge_bases")
    op.drop_column("knowledge_bases", "purpose")
