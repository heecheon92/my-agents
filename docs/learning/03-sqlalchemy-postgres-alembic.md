---
created: 2026-05-17
updated: 2026-08-09
status: active
topics:
  - sqlalchemy
  - postgres
  - alembic
  - migrations
related_code:
  - my_agents/persistence/database.py
  - my_agents/persistence/models.py
  - scripts/dev_pgvector.py
  - .vscode/tasks.json
  - alembic/env.py
  - alembic/versions/20260517_0001_initial_service_schema.py
---

# SQLAlchemy, Postgres, and Alembic

This note explains the database stack in this project from a backend-learning perspective.

## The three layers

| Layer | What it is | In this repo |
| --- | --- | --- |
| SQLAlchemy | Python ORM and database toolkit | Model classes under `my_agents/**/models.py` and the shared `Base` in `my_agents/persistence/database.py` |
| Postgres / Neon | The actual relational database | Configured with `MY_AGENTS_DATABASE_URL`; Neon is managed Postgres |
| Alembic | Migration tool for schema changes | `alembic/env.py` and migration files under `alembic/versions/` |

```mermaid
flowchart LR
    Python["Python service code"] --> SQLAlchemy["SQLAlchemy models / sessions"]
    SQLAlchemy --> Alembic["Alembic migration history"]
    Alembic --> DB[("Postgres / Neon schema")]
    SQLAlchemy --> Queries["runtime queries"]
    Queries --> DB
```

## Why not just use `create_all`?

`Base.metadata.create_all()` is useful for quick local bootstrapping, but it is not a durable production migration strategy. It does not give a human-readable history of schema decisions, and it can hide whether a deployed database is missing a migration.

This project keeps auto-create only for the default in-memory SQLite path so tests stay fast and credential-free. Postgres/Neon should use:

```bash
uv run alembic upgrade head
```

## Mental model

1. You edit SQLAlchemy models when application code needs new persisted data.
2. You create an Alembic migration that describes how the real database changes.
3. You run the migration against Postgres/Neon before running the service against that database.
4. Tests can still use SQLite for fast behavior checks, but deployment-like confidence comes from migration smoke tests.

## Neon-specific reminder

Neon may show a default query that creates `playing_with_neon`. That table is unrelated to this app. For this project, the schema should come from Alembic migrations.

## Debugging a model/schema mismatch

An error such as `psycopg.errors.UndefinedColumn` means the running Python model and the selected database schema disagree. In the 2026-08-09 local case, the model selected `source_knowledge_base_name_snapshot`, but the pgvector database was still at revision `20260615_0028`; revision `20260624_0029` adds that column.

The important diagnostic sequence is:

1. Identify the database used by the running process. A VS Code launch profile may override `.env`.
2. Compare the database's current Alembic revision with the repository head.
3. Run the missing migration against that exact database.
4. Retry the endpoint; changing frontend rendering cannot repair a missing backend column.

`create_all()` is not a substitute here: it creates missing tables but does not evolve an existing table by adding newly mapped columns.

The VS Code pgvector task previously invoked `uv` through a non-interactive task shell. A GUI-launched VS Code process may not inherit the Nix/Homebrew shell path, producing `command not found: uv` before migrations run. Making the task execute the Python extension's selected interpreter, then using `sys.executable` for nested Alembic and pytest commands, keeps the whole operation in one known environment without changing user dotfiles.

## How this project tests migrations safely

The default migration smoke test creates a temporary SQLite database, runs Alembic to
`head`, and compares the resulting schema with `Base.metadata`. A second offline smoke
generates SQL without connecting to a database. The optional Postgres/Neon smoke only runs
when `MY_AGENTS_TEST_DATABASE_URL` points at a dedicated test database.

```mermaid
flowchart TD
    Test["tests/test_migrations.py"] --> SQLite["temporary SQLite upgrade"]
    Test --> Offline["offline SQL generation"]
    Test --> Gate{"MY_AGENTS_TEST_DATABASE_URL set?"}
    Gate -- "no" --> Skip["skip external DB smoke"]
    Gate -- "yes" --> Postgres["run Alembic against dedicated Postgres/Neon DB"]
```

## Small exercise

Answer these before an interview:

1. What is the difference between SQLAlchemy model code and a Postgres table?
2. Why is Alembic better than `create_all` for a deployed service?
3. Why does this project still allow SQLite auto-create for tests?

## Revision history

- 2026-08-09: Added the stale-schema and GUI task PATH failure investigation and interpreter-reuse fix.
- 2026-05-17: Created after adding Alembic/Postgres readiness for the product chat service.
