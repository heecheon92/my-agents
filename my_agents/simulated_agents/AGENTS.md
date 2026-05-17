# AGENTS.md — `my_agents/simulated_agents`

This directory is an agent development lab for learning-only simulated agents, experimentation, and LangGraph architecture practice.

## Purpose

The primary purpose is for the user to learn, experiment, and practice implementing different LangGraph patterns, not to build production-ready agent features.

Examples of patterns to practice here:

- swarm / handoff;
- ReAct;
- orchestration;
- supervisor / worker;
- evaluator / critic;
- routing;
- checkpointer and memory experiments;
- tool-use protocols.

## Priority order

When editing files under this directory, prioritize:

1. accurate implementation of the target architectural pattern;
2. learner readability;
3. explicit inline code over reusable wrappers;
4. small tests or examples that prove the pattern;
5. honest simulation boundaries.

Lower priority:

- reusable abstractions;
- production polish;
- deduplication;
- generic helper layers;
- perfect consistency with production code style.

## Style guidance

- Prefer inline implementation until the user is confident with the primitive concepts.
- Avoid introducing wrapper/helper functions just to make code cleaner.
- It is okay for simulated graphs to be verbose if that makes the pattern easier to follow.
- It is okay for different simulated agents to use different structures when comparing patterns.
- Use comments and local diagrams when they help explain state flow.
- Keep fake tools and dummy data clearly labeled as simulation.

## Safety and boundary

- Do not import simulation code into production API/CLI surfaces unless the user explicitly asks to promote it.
- Do not add real-world side effects unless the simulation explicitly needs a fake/stubbed boundary.
- Do not use real secrets.
- Do not present simulated results as real-world facts.

## Expected workflow

The user may ask Codex for a practice-agent idea. For example, the `mbti` graph is a practice implementation for swarm architecture.

When adding a new simulated agent, document:

- what pattern it practices;
- the graph shape;
- the key state fields;
- what is fake/simulated;
- what would need to change for production use.
