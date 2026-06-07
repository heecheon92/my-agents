# AGENTS.md — `my-agents`

This repository is a backend-only AI-agent project. It is intentionally small, incremental, and learning-oriented.

The current product shape is a FastAPI + LangGraph assistant/router foundation that uses OpenAI GPT variants through `langchain-openai` by default, with deterministic mode kept for offline tests and smoke checks.

## Product intent

- Build a collection of AI-agent backend capabilities incrementally.
- Keep the backend clean, inspectable, and testable before adding complexity.
- Do **not** add frontend code here. Frontend work belongs in a separate repository.

## Current architecture

- `main.py` exposes the ASGI app.
- `my_agents/cli.py` exposes a local terminal chat loop for the current graph.
- `my_agents/api/` owns the FastAPI app factory and route modules.
- `my_agents/agents/general_assistant/graph.py` owns the current general assistant LangGraph `StateGraph`.
- `my_agents/agents/general_assistant/classifier.py` owns deterministic route classification for the general assistant.
- `my_agents/agents/general_assistant/responders.py` owns deterministic and OpenAI-backed reply composition for the general assistant.
- `my_agents/agents/capabilities.py` describes route capability metadata without claiming separate agents executed.
- `my_agents/simulated_agents/` contains learning-only graph experiments that are not production API/CLI surfaces.
- `my_agents/settings.py` owns environment-driven runtime configuration.
- `my_agents/schemas.py` owns Pydantic request/response contracts.
- `tests/` defines the behavior contract and must stay offline by default.

The graph currently has one production assistant/router path. Route labels and capability metadata describe behavior honestly; they are not proof that separate specialized agents ran. Simulation-only graphs live under `my_agents/simulated_agents/`.

## Hard constraints

- No frontend files, UI framework setup, or browser app scaffolding in this repo.
- No provider sprawl. The only planned LLM provider is OpenAI.
- Use `langchain-openai` / `ChatOpenAI` for OpenAI model access, not direct provider calls in application code.
- Keep deterministic mode available for tests and offline smoke checks. The normal local response mode is OpenAI-backed and requires `OPENAI_API_KEY` before chat requests can succeed.
- Never commit real secrets. Do not read or print `.env` contents unless the user explicitly asks and understands the risk.
- Keep `.env.example` safe and secret-free.
- Do not claim live specialized agents, persistent memory, hosted deployment, or frontend functionality unless implemented and tested.

## Environment policy

Default local behavior:

```bash
MY_AGENTS_RESPONSE_MODE=openai
OPENAI_API_KEY=sk-your-project-key
```

Offline deterministic mode is available for tests and credential-free smoke checks:

```bash
MY_AGENTS_RESPONSE_MODE=deterministic
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
- Add dependencies only when they support a clearly implemented milestone and, unless the user has explicitly approved the dependency, do not add them.
- For the approved PDF ingestion milestone, local extraction dependencies such as `pdfplumber`, `docling`, and user-approved PyMuPDF are allowed when they improve reliability; treat PyMuPDF licensing (AGPL/commercial) and Docling's heavyweight OCR/layout dependency footprint as explicit tradeoffs to document before further expansion. Avoid cloud-only extraction stacks unless the user explicitly approves that next stage.
- Prefer existing standard library, FastAPI, Pydantic, LangGraph, and LangChain/OpenAI primitives before adding packages.
- Do not add non-OpenAI LLM provider integrations unless the user explicitly reverses the current provider policy.

## Coding style

- Python target is defined in `pyproject.toml`.
- Use typed Pydantic schemas at API boundaries.
- Keep route handlers thin; put production-surface agent graph/classifier/responder logic under `my_agents/agents/<agent_name>/`.
- Put learning-only or simulation-only architectures under `my_agents/simulated_agents/<agent_name>/`; do not import them into production API/CLI surfaces unless explicitly promoted.
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

For terminal chat smoke testing:

```bash
printf 'Hello\n/exit\n' | MY_AGENTS_RESPONSE_MODE=deterministic uv run python -m my_agents.cli
```

Tests must not require `OPENAI_API_KEY`. Mock LangChain/OpenAI boundaries in tests.

## Incremental roadmap bias

Prefer this sequence:

1. Preserve deterministic classify/router contract.
2. Keep OpenAI-backed reply behavior as the normal local mode while preserving deterministic tests.
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

Agent-level README convention:

- Every concrete production-surface agent implementation folder under `my_agents/agents/<agent_name>/` should have its own bilingual README pair.
- Every concrete simulation-only implementation folder under `my_agents/simulated_agents/<agent_name>/` should also have its own bilingual README pair.
- `README.md` is Korean.
- `README.en.md` is English.
- Agent README files should cross-link to each other near the top, just like the repo-root READMEs.
- Agent README files should explain the agent purpose, file responsibilities, graph/tool flow, current behavior, planned extension seams, and relevant tests.
- Update the agent README pair whenever that agent's behavior, graph shape, tool policy, state contract, or extension guidance changes.

Learning documentation that supports the owner's learning path lives under `docs/learning/`. Keep the root numbered sequence for personal learning logs, and use subfolders such as `docs/learning/agent-lab/` for focused learning tracks that came from conversations. Agent-generated project architecture docs that are not primarily learning logs should live outside `docs/learning/` (for the product chat service, use `docs/product-chat-service/en/`).

Simulated-agent idea references live under `docs/learning/agent-lab/simulated-agent-candidate-materials/`. Use that catalog as optional inspiration when suggesting or bootstrapping learning-only simulated agents, especially when the user asks for practice ideas. It is not mandatory or exclusive; agents may propose other ideas when they better fit the user's current goal.

Rules for learning-oriented work:

- Treat this repo as a study project as well as a codebase.
- When implementation becomes more abstract, decide whether the explanation is for the owner's learning path or for project architecture. Learning-path material belongs under `docs/learning/`; project architecture docs can live in a project-specific docs folder.
- Create root numbered personal learning notes when the user wants a conversation lesson preserved as a personal log.
- Use learning subfolders, such as `docs/learning/agent-lab/`, for focused learning tracks that should not pollute the root numbered sequence.
- Every personal learning note except `docs/learning/README.md` should include front matter with immutable `created`, refreshed `updated`, `status`, `topics`, and `related_code`.
- Every personal learning note should end with a concise `## Revision history` section.
- For new user-requested personal learning notes, prefer `uv run python scripts/learning_log.py` so numbering, front matter, revision history, and index updates stay consistent.
- For non-trivial bugs or model/tool failures, add or update a debug/fix learning note that records the symptom, root cause or hypothesis, rejected fixes, fix/mitigation, tests, and follow-up risks.
- Prefer step-by-step walkthroughs, request lifecycles, diagrams, vocabulary tables, and small exercises.
- Keep learning docs honest about what is implemented now versus future intent.
- Do not replace tests or README docs with learning notes; learning notes are an additional teaching layer.

Mermaid diagram guidance for Markdown work:

- When creating or updating any Markdown file, proactively consider whether a Mermaid diagram would improve readability or comprehension.
- Prefer Mermaid for architecture overviews, request lifecycles, graph/node flows, state transitions, sequence interactions, decision trees, and data relationships.
- Add a diagram only when it clarifies the explanation; do not add decorative diagrams that duplicate simple prose.
- Keep each diagram focused on one concept and place it near the section it explains.
- Use fenced `mermaid` blocks so diagrams stay version-controllable and render on GitHub-compatible surfaces.
- If a diagram would become large or noisy, split it into smaller diagrams or keep the explanation textual.
- Do not default to flowcharts automatically. Choose the Mermaid diagram type by the concept:
  - `flowchart` for architecture, control flow, graph/node flow, pipelines, and decision trees.
  - `sequenceDiagram` for request/response timelines, streaming flows, auth/email handshakes, and service interactions over time.
  - `stateDiagram-v2` for lifecycle states such as conversation runs, ingestion jobs, auth tokens, or deployment phases.
  - `erDiagram` for database tables, ownership relationships, and persistence models.
  - `classDiagram` for Python contracts, dataclasses, service interfaces, and package-level type relationships.
- Prefer `flowchart TD` / `flowchart LR` for new flowcharts, with quoted labels when node text includes spaces, punctuation, underscores, or implementation names that may confuse Markdown renderers.

When behavior changes, update:

- `README.md` and `README.en.md` for user-facing setup and examples.
- `docs/learning/` when the content supports the owner's learning path; use `docs/learning/agent-lab/` for generated agent-lab learning notes and `docs/product-chat-service/en/` for service architecture docs.
- `.env.example` for safe env knobs.
- tests for the behavior contract.
- this `AGENTS.md` if project constraints or architecture conventions change.

## Completion criteria for future agents

A change is complete only when:

- the requested behavior is implemented;
- tests cover the important path without real credentials;
- lint and format checks pass;
- Korean and English README/env docs are accurate if setup changed;
- learning notes are updated when the change introduces new concepts or abstractions;
- no secrets are exposed;
- no frontend or non-OpenAI provider scope was added accidentally.
