---
name: performance-optimizer
description: Use when running a collaborative, measurement-led performance optimization workflow for any subsystem such as RAG/retrieval, ingestion, API latency, database queries, frontend rendering, build time, or memory usage. Supports a multi-turn agent/human loop where baseline metrics are captured first, Codex diagnoses bottlenecks and proposes quality-safe options, the human chooses a direction, Codex implements, then the same scenario is re-measured and documented.
---

# Performance Optimizer

## Purpose

Run a subject-agnostic, measurement-led optimization loop without sacrificing the
quality, correctness, safety, or user-visible behavior that made the current system
valuable.

Use this as a **multi-turn collaborative workflow**, not a one-shot refactor. Prefer
measured timing, resource, and quality output over guesses. The human chooses the
optimization direction unless they already explicitly approved a specific option.

## Core contract

- Do not optimize before a baseline scenario and evaluator/quality guard exist.
- Do not silently trade quality, correctness, safety, authorization, accessibility,
  data integrity, or product semantics for speed.
- Do not claim performance improvement from code inspection or tests alone; compare
  same-scenario before/after measurements.
- Keep metrics and docs redacted: no secrets, tokens, raw private content, user IDs,
  document IDs, emails, prompts, or production-only identifiers.
- Preserve the subject-specific skill's invariants when one applies. For example,
  RAG work should also follow `rag-performance-optimizer`.

## Workflow

### 1. Intake or design pre-optimization metrics

Extract or define:

- scenario label: subject, route/job/component, environment, warm/cold start, data size,
  concurrency, provider/backend, and config profile;
- performance numbers: total time plus phase timings, call counts, rows/items processed,
  memory/CPU/disk/network where relevant;
- quality guard: the behavior or output that must not regress;
- repeatability: exact command, fixture, input, repeat count, and output path.

If no baseline exists, create the smallest safe evaluator first. It can be a benchmark
script, test fixture, manual runbook, browser trace, metrics query, or log-capture command,
but it must produce comparable before/after evidence.

### 2. Diagnose before editing

Respond first with:

1. the dominant bottleneck by measured time/resource and call count;
2. secondary bottlenecks;
3. what is explicitly not the bottleneck;
4. likely root cause in code/data/runtime shape;
5. quality-safe optimization options.

Calculate approximate shares when useful:

```text
phase_share = phase_ms / total_ms * 100
improvement_delta = before_ms - after_ms
improvement_percent = improvement_delta / before_ms * 100
```

### 3. Offer options and wait for human choice

For each option, state:

- expected latency/resource effect;
- quality risk: low / medium / high;
- implementation surface;
- verification and quality guard;
- documentation or ledger update needed.

Prefer options in this order:

1. remove duplicate work;
2. batch N+1 calls/queries/provider operations;
3. reuse work within one request/job/run;
4. narrow broad scans while preserving filters and semantics;
5. reduce object/row/cardinality churn without reducing useful information;
6. add indexes or verify query plans when SQL itself is slow;
7. improve scheduling/concurrency/backpressure;
8. only then tune budgets, sampling, model/provider choices, precision, or quality profiles.

If the user has already clearly chosen an option, proceed without asking again.

### 4. Implement small, reversible changes

Before editing, inspect the real code path and tests. Keep the diff narrow and preserve
the baseline quality guard. Add or update regression coverage for any intentional output
shape change, especially when reducing rows/chunks/candidates/items.

If an optimization changes a contract intentionally, document why it is quality-preserving
or ask before making the tradeoff.

### 5. Validate before claiming improvement

Run:

1. targeted behavior tests for the changed path;
2. the same benchmark/evaluator used for the baseline;
3. lint/format/static checks expected by the repo;
4. any subject-specific quality guard.

Do not claim real improvement until the before/after evaluator passes and the quality
guard is compared.

### 6. Analyze post-optimization metrics

Compare the same scenario:

| Phase | Before calls/items | Before ms/resource | After calls/items | After ms/resource | Delta | Delta % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |

Then report:

- applied change;
- measured improvement or regression;
- quality impact and guard evidence;
- new bottleneck, if any;
- whether more optimization is worthwhile.

### 7. Update durable documentation when appropriate

Record durable before/after data when the repo has a performance ledger or when the
optimization changes an important workflow. Write recent-work-first. Include:

- measurement ID/date;
- scenario and command;
- redacted before/after metrics;
- applied optimization and status;
- quality guard result;
- remaining bottleneck and next recommended option.

## Subject-specific adapters

Use this skill as the common workflow. Layer subject-specific skills on top:

- `rag-performance-optimizer` for RAG/retrieval/ContextForge/embedding/reranking work;
- create or use a subject-specific skill for ingestion, frontend rendering, API latency,
  database operations, build performance, or memory if the subsystem has special invariants.

The subject-specific skill may define required files, tests, docs, and safety invariants,
but it should not bypass this baseline -> diagnosis -> options -> human choice ->
implementation -> post-measurement loop.

## Response shapes

### Pre-optimization analysis

```markdown
Bottleneck: <phase/resource> = <value> (<share>), <calls/items> calls/items.
Secondary: <phase/resource> = <value>.
Not bottleneck: <phase/resource> was cheap.
Likely cause: <code/data/runtime shape>.
Quality-safe options:
1. <option> — expected effect, risk, files, tests.
2. <option> — expected effect, risk, files, tests.
Recommended first action: <one sentence>.
Decision needed: choose option <n>, or approve baseline/evaluator creation first.
```

### Post-optimization summary

```markdown
Applied: <optimization summary>.
Measured improvement:
<table>
Quality impact: <why behavior/safety/output is preserved>.
Docs updated: <files or none>.
Next bottleneck: <phase/resource and suggested next step>.
```
