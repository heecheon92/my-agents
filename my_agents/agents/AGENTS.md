# AGENTS.md — `my_agents/agents`

This directory owns production-surface agent implementations and shared agent metadata.
Follow the repository root `AGENTS.md` first; this file adds agent-folder documentation
requirements.

## Agent folder documentation contract

When adding a new concrete agent folder under `my_agents/agents/<agent_name>/`, or when
updating behavior, graph shape, routing, state contract, tool policy, provider policy, or
extension guidance for an existing agent folder, that agent folder must contain:

- `README.md` — Korean documentation;
- `README.en.md` — English documentation;
- `CHANGELOG.md` — concise rationale/history for why the agent needed each meaningful
  change.

Keep `README.md` and `README.en.md` semantically aligned. They do not need to be literal
line-by-line translations, but both must explain the same behavior and boundaries.

## Required README structure

Each agent README pair should follow this structure, localized for the file language:

1. Title with the agent name.
2. Language cross-link near the top.
3. One-paragraph summary of what the agent is and what product surface calls it.
4. **Current role** — bullet list of what the agent does now and what it does not claim.
5. **File structure** — table mapping important files in the folder to responsibilities.
6. **Graph or execution flow** — Mermaid diagram when the flow has multiple nodes,
   branches, provider boundaries, or service-layer inputs.
7. **Route/tool/state meaning** — explain route labels, tool policy, state fields, or
   other agent-specific contracts that affect behavior.
8. **Capability or boundary metadata** — document whether the behavior is production,
   prototype, deterministic, simulation-only, or provider-backed.
9. **Relationship to service layers** — explain what the agent receives from API/service
   code and what remains outside the agent folder, especially auth, permissions,
   retrieval, ingestion, persistence, and provider secrets.
10. **Extension guidance** — where future tools, providers, nodes, or graph changes should
    be added and what should stay out of scope.
11. **Change checklist** — tests/docs to update when the agent changes.

README files should be honest about current behavior. Do not imply that separate
specialized agents, live tools, persistent memory, external side effects, or production
integrations exist unless they are implemented and tested.

## CHANGELOG expectations

`CHANGELOG.md` should be human-readable and append-only. For each meaningful agent change,
add an entry with:

- date;
- short change title;
- why the change was needed;
- behavior or contract impact;
- verification evidence when available.

Prefer concise entries over exhaustive implementation detail. The changelog should explain
why the agent folder changed, not duplicate every diff.

## Boundary reminders

- Do not put service-layer authorization, database retrieval, ingestion, or provider-secret
  handling directly inside agent graph folders.
- Agent folders may receive already-authorized context and metadata from service layers.
- Keep simulation-only agents under `my_agents/simulated_agents/`, not here, unless the
  user explicitly promotes them to production-surface agents.
