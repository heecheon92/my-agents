# 08 — Postgres, Alembic, and Neon readiness

This milestone turns the v1 service schema into a migration-managed database contract.

## Why this matters

The service now has durable backend concepts: first-party users, server sessions, groups, memberships, document permissions, conversations, knowledge bases, extraction runs, chunks, entity mentions, citations, and agent events. A portfolio-grade service should not rely on production-time `create_all` for that schema.

```mermaid
flowchart TD
    Models["SQLAlchemy models"] --> Metadata["Base.metadata"]
    Metadata --> Alembic["Alembic migration"]
    Alembic --> Neon[("Neon / Postgres")]
    Metadata --> SQLite[("SQLite in-memory tests")]
```

## Current decision

- Alembic owns Postgres/Neon schema creation and future schema changes.
- In-memory SQLite can still auto-create tables for fast offline tests.
- Non-SQLite databases do not auto-create tables unless explicitly overridden for throwaway development.
- pgvector columns are intentionally deferred until real embedding retrieval is implemented.

## Safe Neon flow

1. Create a Neon project/database.
2. Store the connection string only in local `.env` or shell env.
3. Include `sslmode=require` in the URL.
4. Run:

```bash
MY_AGENTS_RESPONSE_MODE=deterministic \
MY_AGENTS_DATABASE_URL='postgresql+psycopg://user:password@host/dbname?sslmode=require' \
uv run alembic upgrade head
```

Do not run Neon's default `playing_with_neon` sample query for this app. It creates unrelated demo data; this service schema is managed by Alembic.

## Verification

The offline verification path is:

```bash
uv run pytest -q
uv run ruff check . --no-cache
uv run ruff format --check .
```

Optional Postgres/Neon smoke tests must stay gated by `MY_AGENTS_TEST_DATABASE_URL` and must never print the real URL.

```bash
MY_AGENTS_TEST_DATABASE_URL='postgresql+psycopg://user:password@host/test_db?sslmode=require' \
uv run pytest tests/test_migrations.py -q
```

Leave `MY_AGENTS_TEST_DATABASE_URL` unset for normal offline verification; the external
database smoke test will skip automatically.
