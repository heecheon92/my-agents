"""Identify embedding spaces; existing vectors remain unknown until re-indexed."""

import sqlalchemy as sa

from alembic import op

revision = "20261017_0041"
down_revision = "20261017_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_indexes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(240), nullable=False),
        sa.Column("model", sa.String(240), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("encoding", sa.String(240), nullable=False),
        sa.Column("preprocessing", sa.String(240), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    for table in ("document_chunks", "document_metadata_profiles"):
        name = f"fk_{table}_embedding_index"
        if op.get_bind().dialect.name == "sqlite":
            # Nullable inline REFERENCES supports both online and offline SQLite DDL,
            # without rebuilding existing tables or guessing their historical shape.
            op.execute(
                f"ALTER TABLE {table} ADD COLUMN embedding_index_id VARCHAR(64) "
                f"CONSTRAINT {name} REFERENCES embedding_indexes (id)"
            )
        else:
            op.add_column(
                table,
                sa.Column(
                    "embedding_index_id",
                    sa.String(64),
                    sa.ForeignKey("embedding_indexes.id", name=name),
                    nullable=True,
                ),
            )
        op.create_index(f"ix_{table}_embedding_index_id", table, ["embedding_index_id"])
    # No guessed backfill: identical dimensions do not prove which model generated a row.


def downgrade() -> None:
    for table in ("document_chunks", "document_metadata_profiles"):
        op.drop_index(f"ix_{table}_embedding_index_id", table_name=table)
        if op.get_bind().dialect.name != "sqlite":
            op.drop_constraint(f"fk_{table}_embedding_index", table, type_="foreignkey")
        op.drop_column(table, "embedding_index_id")
    op.drop_table("embedding_indexes")
