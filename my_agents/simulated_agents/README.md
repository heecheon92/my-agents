# Simulated agents

This directory is an agent development lab for learning, experimentation, and implementing different LangGraph architectural patterns.

Unlike `my_agents/agents/`, this directory is **not** part of the production/API surface. Code here is allowed to be more tutorial-like, explicit, and concept-shaped.

## Primary purpose

The primary purpose is to act as an agent development lab where I can learn, experiment with, and implement patterns such as:

- swarm / handoff architectures;
- ReAct-style loops;
- orchestrator-worker flows;
- evaluator / critic loops;
- routing graphs;
- memory/checkpointer experiments;
- tool-use protocols;
- multi-agent coordination patterns.

The goal is not to build reusable production abstractions. The goal is to correctly understand and implement the architecture being studied.

## Code quality priorities

For this directory, prioritize:

1. **Accuracy of the architectural pattern**
2. **Readability for learning**
3. **Inline, explicit implementation**
4. **Traceability of state transitions**
5. **Small tests that prove the pattern shape**

Do not over-prioritize:

- reusable wrappers;
- generic abstractions;
- production polish;
- deduplication;
- packaging elegance.

A little duplication is acceptable when it makes the pattern easier to see.

## Workflow

The normal workflow for this directory is:

1. I ask Codex for a good practice-agent idea.
2. We choose the LangGraph pattern being practiced.
3. We implement a small simulated graph for that pattern.
4. We keep the graph honest about being a simulation.
5. We add just enough tests/docs to confirm the architecture.

Example:

```text
mbti/ -> practice swarm / handoff architecture
study_coach/ -> practice planner / executor / critic revision loop
debate_council/ -> practice sequential multi-agent orchestration / moderator synthesis
reducer_playground/ -> practice state reducers and parallel merge rules
support_ticket_router/ -> practice conditional routing with structured route labels
```

The MBTI graph is not meant to be a real MBTI product. It is a practice graph for understanding how swarm-style active-agent handoff works.

## Folder expectations

Each simulated agent folder should include, when useful:

```text
README.md          # what pattern this practices
README.en.md       # optional English/Korean pair if needed
AGENTS.md          # optional extra local constraints
*.py               # graph implementation and experiments
```

Keep documentation focused on:

- the pattern being practiced;
- the graph shape;
- the important state fields;
- where handoff/routing/tool execution happens;
- what is intentionally fake or simplified.

## Boundary

Simulation code should not be imported into production API/CLI surfaces unless explicitly promoted.

Production-surface code belongs in:

```text
my_agents/agents/
```

Learning-only simulation code belongs here:

```text
my_agents/simulated_agents/
```
