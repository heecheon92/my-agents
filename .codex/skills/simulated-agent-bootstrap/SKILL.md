---
name: simulated-agent-bootstrap
description: Create a learning-only simulated agent scaffold for this FastAPI/LangGraph backend repository. Use when the user asks to bootstrap, scaffold, start, or create a new folder under `my_agents/simulated_agents/agent_slug/` with bilingual README snippets, `__init__.py`, and a starter `graph.py` terminal while loop, following the existing `mbti` and `study_coach` simulated-agent conventions.
---

# Simulated Agent Bootstrap

## Overview

Create the first folder structure and documentation snippets for a new learning-only simulated agent. Include a minimal `graph.py` terminal loop so the user can immediately practice CLI-driven graph invocation after designing the LangGraph pattern.

## Workflow

1. Confirm the target is a simulation/learning agent, not a production API/CLI agent.
2. Normalize the requested agent name to a Python package slug: lowercase `snake_case`.
3. Run the bundled scaffold script from the repository root:

```bash
python3 /path/to/simulated-agent-bootstrap/scripts/create_simulated_agent.py <agent-name>
```

If running outside the repo root, pass the simulated-agent directory explicitly:

```bash
python3 /path/to/simulated-agent-bootstrap/scripts/create_simulated_agent.py <agent-name> \
  --simulated-root /path/to/repo/my_agents/simulated_agents
```

4. Keep `graph.py` as a bootstrap loop only: `respond()` is a placeholder, and the `while True` CLI loop mirrors the existing simulated-agent examples. Do not add real graph nodes unless the user separately asks for implementation.
5. Report the created path and files.

## Scaffold shape

The script creates:

```text
my_agents/simulated_agents/<agent_slug>/
├── README.md       # Korean bootstrap snippet
├── README.en.md    # English bootstrap snippet
├── __init__.py     # package marker
└── graph.py        # starter CLI while loop with respond() placeholder
```

The README snippets include:

- cross-links between Korean and English variants;
- the simulated-agent purpose;
- a small Mermaid draft graph placeholder;
- file responsibility table;
- reminders not to connect simulation code to production surfaces.

The generated `graph.py` includes:

- `AGENT_NAME`;
- `respond(user_input: str)` placeholder;
- `if __name__ == "__main__"`;
- `while True` terminal loop with `/quit`, `/exit`, `/q`, `KeyboardInterrupt`, and generic exception handling.

## Reference conventions

Use `my_agents/simulated_agents/mbti` and `my_agents/simulated_agents/study_coach` as the local examples for folder shape, learner-facing documentation style, and the terminal chat loop pattern.

Respect the simulated-agent boundary:

- do not import simulation code into production API/CLI surfaces;
- prefer inline, readable LangGraph code when implementation is later requested;
- keep fake tools and side effects clearly labeled as simulation.

## Safety

The script refuses to overwrite existing files by default. Use `--overwrite` only when the user explicitly wants to regenerate an existing bootstrap.
