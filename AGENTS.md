# AGENTS.md — `my-agents`

This repository is a backend-only AI-agent project. It is intentionally small, incremental, and learning-oriented.

The current product shape is a FastAPI + LangGraph assistant/router foundation that can run deterministically by default and optionally generate replies with OpenAI GPT variants through `langchain-openai`.

## Product intent

- Build a collection of AI-agent backend capabilities incrementally.
- Keep the backend clean, inspectable, and testable before adding complexity.
- Do **not** add frontend code here. Frontend work belongs in a separate repository.

## Current architecture

- `main.py` exposes the ASGI app.
- `my_agents/api.py` owns the FastAPI app factory and routes.
- `my_agents/graph.py` owns the LangGraph `StateGraph`.
- `my_agents/classifier.py` owns deterministic route classification.
- `my_agents/responders.py` owns deterministic and OpenAI-backed reply composition.
- `my_agents/settings.py` owns environment-driven runtime configuration.
- `my_agents/schemas.py` owns Pydantic request/response contracts.
- `tests/` defines the behavior contract and must stay offline by default.

The graph currently has one assistant/router path. Route labels are metadata for future capabilities, not proof that specialized agents ran.

## Hard constraints

- No frontend files, UI framework setup, or browser app scaffolding in this repo.
- No provider sprawl. The only planned LLM provider is OpenAI.
- Use `langchain-openai` / `ChatOpenAI` for OpenAI model access, not direct provider calls in application code.
- Keep deterministic mode as the default. The app must run and tests must pass without credentials.
- Never commit real secrets. Do not read or print `.env` contents unless the user explicitly asks and understands the risk.
- Keep `.env.example` safe and secret-free.
- Do not claim live specialized agents, persistent memory, hosted deployment, or frontend functionality unless implemented and tested.

## Environment policy

Default local behavior:

```bash
MY_AGENTS_RESPONSE_MODE=deterministic
```

OpenAI-backed reply generation is opt-in:

```bash
MY_AGENTS_RESPONSE_MODE=openai
OPENAI_API_KEY=sk-your-project-key
MY_AGENTS_OPENAI_MODEL=gpt-5.5
```

Optional knobs:

```bash
MY_AGENTS_OPENAI_TIMEOUT_SECONDS=30
MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS=300
MY_AGENTS_OPENAI_REASONING_EFFORT=low
MY_AGENTS_OPENAI_VERBOSITY=low
```

If adding conversation memory later, prefer LangGraph checkpointers as the app-owned source of truth. OpenAI `previous_response_id` may be stored as one field inside graph state, but should not replace application state.

## Dependency policy

- Use `uv` for dependency management.
- Add dependencies only when they support a clearly implemented milestone.
- Prefer existing standard library, FastAPI, Pydantic, LangGraph, and LangChain/OpenAI primitives before adding packages.
- Do not add non-OpenAI LLM provider integrations unless the user explicitly reverses the current provider policy.

## Coding style

- Python target is defined in `pyproject.toml`.
- Use typed Pydantic schemas at API boundaries.
- Keep route handlers thin; put graph logic in `my_agents/graph.py` and provider logic in `my_agents/responders.py`.
- Keep graph state explicit and small.
- Keep responses honest: classify, explain, and disclose current behavior.
- Prefer small, reversible changes with tests.

## Test and verification commands

Run before claiming completion:

```bash
uv run pytest -q
uv run ruff check . --no-cache
uv run ruff format --check .
```

For API smoke testing:

```bash
MY_AGENTS_RESPONSE_MODE=deterministic uv run uvicorn main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/assistant/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Plan my next backend milestone","history":[]}'
```

Tests must not require `OPENAI_API_KEY`. Mock LangChain/OpenAI boundaries in tests.

## Incremental roadmap bias

Prefer this sequence:

1. Preserve deterministic classify/router contract.
2. Add OpenAI-backed reply behavior behind env flags.
3. Add LangGraph checkpointer-backed short-term memory.
4. Store OpenAI `previous_response_id` only after graph thread state exists.
5. Add real tool/function capabilities one at a time with tests.
6. Add durable storage only when the behavior requires it.
7. Keep frontend separate.

## Documentation expectations

This repository maintains two README files:

- `README.md` is the Korean README.
- `README.en.md` is the English README.

Rules for README maintenance:

- Always update both README files when user-facing setup, behavior, architecture, commands, or examples change.
- Each README must include a clear link to the other language variant near the top.
- Keep the two README files semantically equivalent, even if wording is localized rather than line-for-line translated.
- Do not leave one README stale after changing the other.
- If only one README exists when documentation work starts, create or restore the missing language variant.

When behavior changes, update:

- `README.md` and `README.en.md` for user-facing setup and examples.
- `.env.example` for safe env knobs.
- tests for the behavior contract.
- this `AGENTS.md` if project constraints or architecture conventions change.

## Completion criteria for future agents

A change is complete only when:

- the requested behavior is implemented;
- tests cover the important path without real credentials;
- lint and format checks pass;
- Korean and English README/env docs are accurate if setup changed;
- no secrets are exposed;
- no frontend or non-OpenAI provider scope was added accidentally.
