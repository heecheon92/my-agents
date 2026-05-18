---
name: simulated-agent-implementation-review
description: Review learning-only simulated agent implementations under `my_agents/simulated_agents/agent_slug/` and create `FEEDBACK.md` plus a production-shaped reference implementation such as `graph_reference.py`. Use when the user asks for review/feedback of a simulated agent graph, wants an experienced AI engineer's implementation style, or wants guidance on LangGraph state handling, prompt payloads, direct graph invocation, and thin CLI adapter boundaries.
---

# Simulated Agent Implementation Review

## Overview

Use this skill to review a learning-only simulated LangGraph agent and produce two artifacts in the agent folder:

1. `FEEDBACK.md` — concise, learner-facing review of the current implementation.
2. `graph_reference.py` — an experienced-engineer reference implementation that preserves the same learning pattern but improves state handling, prompt clarity, graph invocation, and readability.

Keep the existing user implementation intact unless the user explicitly asks you to edit it. The reference file is a comparison artifact, not a replacement. Do not mark the bootstrap `respond()` pattern as wrong by itself; it is valid as a beginner-friendly CLI adapter when it stays thin.

## Workflow

1. Identify the simulated agent folder, usually `my_agents/simulated_agents/agent_slug/`.
2. Read local scoped guidance, especially `my_agents/simulated_agents/AGENTS.md` if present.
3. Inspect the current implementation files, usually `graph.py`, `README.md`, and `README.en.md`.
4. Review for:
   - graph shape and edge correctness;
   - state contract accuracy;
   - required vs optional state fields;
   - clean state payloads vs provider message objects;
   - prompt role separation (`SystemMessage` for role/instructions, `HumanMessage` for task payload);
   - direct graph invocation clarity versus thin `respond()` adapter boundaries;
   - fake/simulation boundaries;
   - learner readability.
5. Create or update `FEEDBACK.md` in the agent folder.
6. Create or update `graph_reference.py` in the agent folder.
7. Update the agent README pair if file responsibilities or learning guidance changed.
8. Run lightweight validation: `ruff check`, `ruff format --check`, and `py_compile` for the changed Python files. Run the full test suite only when changes touch shared behavior or existing tests.

## FEEDBACK.md requirements

Write feedback as a teaching artifact, not a PR takedown.

Include:

- review target path;
- overall verdict;
- a Mermaid graph of the current shape when useful;
- what the implementation does well;
- prioritized improvement points;
- concrete code examples for state and invocation fixes;
- a short suggested next learning target.

Do not claim the simulated roles are real independent agents. Describe them as graph nodes or simulated roles.

## graph_reference.py requirements

Create a runnable reference implementation that keeps the same conceptual pattern as the user's graph.

Prefer these conventions:

- Use `TypedDict` with required initial input fields and `NotRequired[...]` for node-produced fields.
- Store clean strings in graph state unless the learning point specifically requires message objects.
- Use `state[...]` for values guaranteed by prior graph edges; let invariant violations fail loudly.
- Use `state.get(...)` only for genuinely optional branches.
- Split prompt intent cleanly:
  - `SystemMessage`: role, constraints, output expectations;
  - `HumanMessage`: user question and prior node outputs.
- Prefer direct `graph.invoke(initial_state)` in `graph_reference.py` when the learning goal is state flow and invocation clarity. If keeping `respond()`, make it a very thin adapter that only builds initial state, invokes the graph, and extracts the final output.
- Keep code explicit and local. Do not introduce reusable helper layers unless they clarify the pattern.
- Keep fake tools, fake data, and simulated boundaries obvious.

If the original graph uses OpenAI-backed calls, the reference may also use OpenAI-backed calls. Do not add non-OpenAI providers.

## Reference details

For a compact pattern checklist, artifact skeletons, and the bootstrap-to-reference path, read `references/review-artifacts.md` when creating the artifacts.
