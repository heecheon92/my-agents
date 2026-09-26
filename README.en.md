# my-agents

English | [한국어](./README.md)

**Permission-aware Agentic RAG Backend** — the backend for an AI chat product that searches personal documents, group-shared documents, and administrator-provided reference knowledge only within explicit authorization boundaries, and records both user-visible citations and internal execution evidence.

[Live service](https://www.my-agents.dev) · [Frontend repository](https://github.com/heecheon92/my-agents-frontend) · [Implementation status](./docs/implementation-tracking.md) · [Roadmap](./ROADMAP.md)

> [!NOTE]
> The service is deployed and running, but new accounts are approved manually. Use guest access to look around without waiting for approval.

## Three-minute overview

`my-agents` is more than a RAG example. It connects the whole flow a real product needs in one backend: **authentication → authorization → document ingestion → hybrid retrieval → LangGraph execution → SSE streaming → citation/audit persistence**.

| Question | Answer |
| --- | --- |
| What did I build? | A FastAPI + LangGraph backend that answers from personal, group-shared, and administrator-provided documents, and cites only sources the user is allowed to see |
| What made it hard? | Permission boundaries that come before retrieval quality, server-owned conversation state, ingestion, streaming, and observability that is useful in production |
| What did I verify? | A key-free offline test suite, permission regressions, production smoke paths, and before/after ingestion and retrieval profiles |
| Where is it now? | The core flow is deployed and running; load handling and security review continue |

## Engineering highlights

- **Permission-first retrieval**: chunks the user cannot see are removed before ranking, graph expansion, and prompt construction.
- **Hybrid retrieval**: pgvector vector search and BM25 keyword search gather candidates independently, merge them with RRF (`k=60`), then rerank and pack the context.
- **Bounded full-document review**: only an explicit whole-document request reads one authorized document, and the answer says whether the review was complete or covered only the first range.
- **Inspectable orchestration**: a LangGraph state machine connects the retrieval decision, retrieval, opt-in memory, and response composition as visible stages.
- **Application-owned state**: the application database stores conversations, runs, messages, citations, and redacted events. Temporary LangGraph execution state is never the source of truth for what users see.
- **Streaming as a contract**: SSE carries progress, execution traces, answer deltas, and terminal status while the server persists the same run.
- **Key-free verification**: the LLM, embedding, and reranker boundaries accept deterministic test doubles, so the full suite runs without API keys.

## Measured performance improvements

These are same-scenario **local measurements**, not public SLA claims. Each change followed profiling of a slow path, and search-result shape and document-processing quality checks stayed the same before and after.

| Area | Change | Before | After | Result |
| --- | --- | ---: | ---: | ---: |
| 195-page PDF ingestion, end to end | Run metadata generation concurrently with embedding/indexing, and skip a redundant parse for native-text PDFs | 36.16s | 16.57s | about 54% faster |
| Hybrid retrieval candidate gathering | Defer large columns ranking does not need, and fetch full records only for the final top candidates | 31.42s | 1.84s | 94.1% faster |
| BM25 corpus/rank/hydration | Build the corpus from IDs and text instead of full ORM rows, then fetch only the top results | 14.34s | 0.14s | 99.0% faster |

The [performance logs](./docs/performance/README.md) record exact scenarios and remaining bottlenecks.

## Architecture

```mermaid
flowchart LR
    Client["Browser or API client"] --> Frontend["Separate Next.js frontend"]
    Frontend --> API["FastAPI product API"]
    API --> Auth["Auth, sessions, and groups"]
    API --> Knowledge["Knowledge bases and documents"]
    API --> Runs["Conversations, runs, and SSE"]
    Knowledge --> Ingestion["Parse, chunk, enrich, and embed"]
    Auth --> DB[("Postgres / Neon with pgvector")]
    Ingestion --> DB
    Runs --> DB
```

A single chat request follows this flow:

```mermaid
flowchart TD
    Run["Conversation run"] --> Gate{"Use authorized knowledge?"}
    Gate -->|No| Memory["Governed opt-in memory"]
    Gate -->|Yes| Choice{"Choose a retrieval tool"}
    Choice -->|Focused| Focused["Permission-first hybrid retrieval"]
    Choice -->|Comprehensive| Full["Resolve and read one authorized document"]
    Focused --> Context["Packed context and evidence"]
    Full --> Context
    Context --> Memory
    Memory --> Answer["OpenAI or deterministic response"]
    Answer --> Audit["Persist answer, citations, and redacted events"]
```

### Boundaries enforced during a request

1. The API layer validates the session, CSRF, and group/knowledge-base access.
2. Orchestration decides whether the question needs authorized knowledge and, if so, chooses between focused chunk search and a full-document read.
3. Backend code, not the model, decides which documents may be read and how much. A full-document read is limited to one user-selectable personal or group document.
4. Large documents are read only up to a fixed range, and the answer always carries a partial-review disclosure.
5. The answer, citations, coverage, summarized trace, and redacted events are stored under the same run. Raw full-document text is never kept in checkpoints or events.

Administrator-provided reference knowledge is background context added to the model automatically, not a user-visible source. Its provenance stays in internal audit records, and public responses omit its identifiers, filenames, snippets, and citations.

Production runs one assistant orchestration flow with a retrieval subworkflow. The words `agent` and `graph` name control boundaries in the code; they do not mean several agents run as independent services.

## Product capabilities

- Email/password signup and verification, sessions, CSRF, password reset, and approval-gated guest access
- Invite-only group membership and a personal-to-group sharing request and approval flow
- Personal, group, and administrator-provided knowledge bases with document-level permissions
- PDF, Markdown, plain text, `.xlsx`, `.pptx`, and `.docx` upload and ingestion (PyMuPDF first, with pypdf, Docling, and Tesseract fallbacks)
- Hybrid retrieval that merges pgvector and BM25 with RRF, plus optional cross-encoder reranking
- Full-document review with complete/partial coverage disclosure and range-backed citations
- Server-owned conversation/run history, SSE streaming, answer-supporting citations, and redacted agent events
- User-enabled experimental long-term memory
- Prometheus metrics and local retrieval/ingestion profilers

## Technology stack

| Area | Technology |
| --- | --- |
| API / application | Python 3.14, FastAPI, Pydantic |
| Agent / model | LangGraph, `langchain-openai`, `ChatOpenAI` |
| Persistence | SQLAlchemy, Alembic, PostgreSQL/Neon, pgvector |
| Retrieval | Vector search, BM25Okapi, RRF, optional BAAI cross-encoder |
| Document processing | PyMuPDF, pypdf, Docling, Tesseract, openpyxl, python-pptx |
| Streaming / observability | SSE, Prometheus metrics, redacted run events |
| Quality / delivery | pytest, Ruff, uv, Docker, Render |

## Repository map

```text
my_agents/
├── api/                       # FastAPI routes and thin HTTP boundaries
├── agents/                    # LangGraph orchestration and retrieval workflows
├── auth/ and permissions/     # Session, CSRF, group/document authorization
├── knowledge/                 # Upload, parsing, ingestion, retrieval
├── conversations/             # Server-owned transcript/run models
├── memory/                    # Opt-in memory policy and database persistence
└── persistence/               # SQLAlchemy database boundary

tests/                         # Offline behavior and regression contracts
alembic/                       # PostgreSQL schema migrations
docs/                          # Architecture, operations, performance evidence
scripts/                       # Smoke, benchmark, migration, operator utilities
```

| Behavior to inspect | Code location |
| --- | --- |
| Decide whether a question needs retrieval and compose the response | `my_agents/agents/general_assistant/` |
| Input/output contract between the assistant and permission-aware retrieval | `my_agents/agents/rag_agent/` |
| Query planning, candidate fusion, reranking, and context packing | `my_agents/agents/context_forge/` |

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14. These commands need no API keys.

```bash
uv sync
cp .env.example .env
MY_AGENTS_RESPONSE_MODE=deterministic uv run fastapi dev main.py
curl http://127.0.0.1:8000/health   # in another terminal
```

For real OpenAI responses, set `OPENAI_API_KEY` in `.env` and use `MY_AGENTS_RESPONSE_MODE=openai`. OpenAPI is served at `http://127.0.0.1:8000/openapi.json`.

- Run with the frontend and PostgreSQL: [frontend demo runbook](./docs/product-chat-service/en/10-frontend-demo-runbook.md)
- Environment variables, optional features, operator steps, and frontend contracts: [runtime and integration reference](./docs/product-chat-service/en/34-runtime-and-integration-reference.md)

## Verification

```bash
uv run pytest -q
uv run ruff check . --no-cache
uv run ruff format --check .
git diff --check
```

On 2026-09-26, the full offline suite reports **624 passed, 14 skipped** without real credentials.

## Security and privacy boundaries

- Real secrets and local databases are never committed. `.env.example` holds placeholders only.
- Public-release checks cover the current tree and the full Git history. An exposed credential is revoked and rotated even if its file was later deleted.
- Retrieval permissions are enforced in application code, not through prompt instructions.
- Metrics and default events exclude raw prompts, document text, emails, credentials, and provider traces.
- Reference-knowledge management is limited to privileged administrator account types, with no public role-change API.
- The service is publicly reachable, so it assumes users will not upload sensitive, regulated, or irreplaceable documents.

## Current limitations and next steps

- Signup is manually approved, so this is not yet a self-service product.
- The ingestion worker polls the database; durable queueing, supervision, and stale-job recovery still need work.
- Object storage for uploaded originals, document versioning/re-ingestion, and account deletion/export are not implemented yet.
- Cross-encoder cold starts and PDF processing time remain constraints on small instances.
- Full-document review of large documents currently stops after the first range; reading and synthesizing multiple ranges is future work.
- Shared rate limiting across instances, a production security review, and automated migration/smoke gates are still needed.
- Non-retrieval tools, a background execution scheduler, and production multi-agent orchestration remain roadmap work.

## Selected documentation

- [Implementation and verification status](./docs/implementation-tracking.md)
- [Documentation map and lifecycle](./docs/README.md)
- [Runtime and integration reference](./docs/product-chat-service/en/34-runtime-and-integration-reference.md)
- [Permission-aware RAG design](./docs/product-chat-service/en/06-permission-aware-rag.md)
- [Assistant orchestration flow](./my_agents/agents/general_assistant/README.en.md)
- [Retrieval subworkflow and context assembly](./my_agents/agents/rag_agent/README.en.md)
- [Performance evidence](./docs/performance/README.md)
- [Production smoke evidence](./docs/product-chat-service/en/16-production-smoke-evidence-2026-06-06.md)
- [Operational and migration commands](./scripts/README.md)

See [ROADMAP.md](./ROADMAP.md) for the larger direction and unfinished work.
