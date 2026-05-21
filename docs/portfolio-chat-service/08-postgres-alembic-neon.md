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
- The `20260521_0007` migration enables the Postgres `vector` extension and adds a nullable `document_chunks.embedding_vector` pgvector column. SQLite uses a text-compatible fallback column so offline tests and JSON cosine retrieval remain deterministic.

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

## Local Docker pgvector flow

Use this when you want to exercise the Postgres/pgvector path locally without a hosted
database. The helper pulls DockerHub image `pgvector/pgvector:pg17` by default, starts a
local container on `127.0.0.1:5433`, writes an ignored `.env.pgvector.local`, and can run
Alembic migrations against that database.
The local env file also overrides auth email delivery to `local` and enables the dev outbox
so local signup never calls SMTP/Resend.

```bash
uv run python -m scripts.dev_pgvector up --migrate
set -a; source .env.pgvector.local; set +a
MY_AGENTS_RESPONSE_MODE=deterministic uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

Useful follow-up commands:

```bash
uv run python -m scripts.dev_pgvector test      # gated migration smoke against local pgvector
uv run python -m scripts.dev_pgvector down      # stop the container, keep the Docker volume
```

The generated env file contains only local disposable credentials and is ignored by git.
If you override the password with `MY_AGENTS_PGVECTOR_PASSWORD`, keep that value local and
do not paste it into reports. After switching from SQLite to this local Postgres, re-ingest
documents so the Postgres-only `embedding_vector` column is populated.

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

Existing chunks created before this migration have `embedding_vector = NULL` until they are
reingested or backfilled. Retrieval falls back to the existing JSON embedding path when no
pgvector candidates are available, so rollout can be additive.
