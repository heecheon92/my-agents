---
created: 2026-05-15
updated: 2026-05-15
status: active
topics:
  - simulation
  - agent-architecture
  - learning
  - coding-style
related_code:
  - my_agents/simulated_agents/mbti/graph.py
  - my_agents/simulated_agents/mbti/swarm_reference.py
  - AGENTS.md
---

# Convention: simulated agents may favor learning clarity over production consistency

## Decision

Simulated agent implementations may intentionally deviate from the rest of the codebase when that improves learning value.

The normal production-oriented style still applies to real-world agents, API boundaries, shared infrastructure, settings, schemas, and safety-sensitive code. Simulation agents and architecture experiments live under `my_agents/simulated_agents/` and are not imported into production API/CLI surfaces unless explicitly promoted.

## Why

The project has two goals:

1. build useful backend agent capabilities over time;
2. study and compare agentic architectures such as ReAct, orchestration, swarm/team patterns, evaluator loops, memory strategies, and tool-use protocols.

Those goals sometimes prefer different code shapes.

Production code usually wants:

- small abstractions;
- reusable utilities;
- consistency;
- minimal duplication;
- stable interfaces.

Learning simulations may want:

- explicit steps;
- duplicated-but-readable phases;
- verbose names;
- inline comments;
- toy data close to the algorithm;
- diagrams and trace-friendly structure;
- files organized around concepts rather than maximum reuse.

## Rule

A simulated agent can use a tutorial-style implementation if it is clearly labeled as simulation.

Acceptable simulation deviations include:

- more verbose code than production paths;
- local dummy data or fixtures near the experiment;
- duplicated orchestration steps when duplication helps comparison;
- concept-first file names such as `react_loop.py`, `critic_pass.py`, or `swarm_turns.py`;
- additional comments explaining why a pattern exists;
- explicit intermediate state objects for teaching and tracing;
- architecture diagrams in the agent README.

Still required:

- no real secrets;
- no accidental real-world side effects;
- tests that prove the simulation contract;
- honest capability metadata with `mode="simulation"`;
- clear README explanation of the concept being demonstrated;
- no pollution of shared production utilities unless the abstraction is genuinely reusable.

## Boundary

This exception does **not** apply to:

- real-world agents;
- public API schemas;
- auth/security code;
- OpenAI provider boundaries;
- database access;
- deployment code;
- shared settings and secret handling.

Those areas should remain consistent, small, and production-shaped.

## Recommended folder pattern

A simulated agent can be organized around the concept it teaches:

```text
my_agents/simulated_agents/react_simulation/
  README.md
  README.en.md
  graph.py
  react_loop.py
  fixtures.py
  trace.py
```

The README should explain:

- which architecture pattern is being demonstrated;
- what is simulated;
- what real-world behavior is intentionally missing;
- how to run or test the simulation;
- what to compare against future real-world agents.

Production-surface agents belong under:

```text
my_agents/agents/<agent_name>/
```

Simulation-only agents belong under:

```text
my_agents/simulated_agents/<agent_name>/
```

## Mental model

```text
real_world agent code   -> product-shaped, reusable, safety-first
simulation agent code   -> concept-shaped, readable, traceable, honest
shared infrastructure   -> production-shaped regardless of who uses it
```

## Revision history

- 2026-05-15: Created learning log for `Convention: simulated agents may favor learning clarity over production consistency`.

## Revision history

- 2026-05-15: Created learning log for `Convention: simulated agents may favor learning clarity over production consistency`.
