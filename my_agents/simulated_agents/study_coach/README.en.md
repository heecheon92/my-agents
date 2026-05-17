# Study Coach simulated agent

[한국어](./README.md)

This folder is an agent development lab for practicing the **Planner → Executor → Critic loop** pattern.

`graph.py` is now a runnable learning graph. The goal is not production polish; the goal is to practice translating requirements into LangGraph nodes, state, routing, and stop conditions.

## Pattern to practice

```text
User
  ↓
Planner
  ↓
Executor
  ↓
Critic
  ├── approved → END
  └── revise → Executor
```

The core idea is that one agent does not answer directly. Instead, separate roles improve the final answer quality.

- **Planner**: decides what to teach and in what order.
- **Executor**: writes the actual explanation from the plan.
- **Critic**: evaluates whether the explanation is good enough and requests revision if needed.
- **Route function**: reads the Critic result and revision count, then chooses whether to end or return to Executor.

## Agent goal

When the user enters a difficult development topic, Study Coach should create a small learning plan, teach the topic, then review whether the explanation is beginner-friendly enough.

Example input:

```text
I want to understand LangGraph conditional edges.
```

## Required behavior

### 1. Planner node

The Planner does not directly produce the final user answer.

It creates a `Plan` structured output and stores it in `plan` state.

```python
{
    "topic": "LangGraph conditional edges",
    "learning_goal": "Understand how routing decisions move graph execution",
    "steps": [
        "Explain what conditional edges are",
        "Show a tiny example",
        "Give one practice question",
    ],
}
```

Planner responsibilities:

- identify the topic the user wants to learn
- define a short learning goal
- create teaching steps
- pass the plan to the Executor node

### 2. Executor node

The Executor teaches the user based on the Planner's plan.

The Executor output should include:

- a simple explanation
- an example
- one exercise or next learning action

If the Critic rejected the previous draft, Executor should use `critic_decision.revision_instruction` to revise the answer.

### 3. Critic node

The Critic reviews the Executor's answer.

It creates a `DraftReview` structured output and stores it in `critic_decision` state.

```python
{
    "approved": True,
    "reason": "The answer includes a simple explanation, an example, and one exercise.",
    "revision_instruction": "",
}
```

Or, if revision is needed:

```python
{
    "approved": False,
    "reason": "The answer is too abstract for a beginner.",
    "revision_instruction": "Rewrite with a smaller concrete LangGraph example.",
}
```

Critic approval criteria:

- Does it include a simple explanation?
- Does it include a concrete example?
- Does it include one exercise or next action?
- Does it avoid overly abstract language?

## Loop rule

If the Critic approves, end the graph.

If the Critic rejects, return to Executor for revision.

To avoid infinite loops, the maximum revision count is 2.

```python
if verdict.approved:
    return "END"

if revision_count >= 2:
    return "END"

return "executor"
```

## State design

The current implementation names the shared graph state `StudyCoachState`.

```python
class StudyCoachState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    topic: str
    plan: Plan
    draft_answer: str
    critic_decision: DraftReview
    revision_count: int
```

It is not called `AgentState` because the state belongs to the whole Study Coach graph, not to a single agent/node.

## How to run

An OpenAI API key is required.

```bash
uv run python -m my_agents.simulated_agents.study_coach.graph
```

Exit with:

```text
/exit
```

The script intentionally prints debug logs for learning:

```text
[planner] creating learning plan
[executor] writing draft answer
[critic] reviewing draft answer
[route] deciding next node
[final answer]
```

## Learning points

This graph practices a different pattern from the MBTI swarm example.

- MBTI example: agent handoff / swarm pattern
- Study Coach example: generate → evaluate → revise loop pattern

This pattern is common in real agent systems:

- code writer → code reviewer
- answer generator → evaluator
- researcher → fact checker
- planner → executor → verifier

## Implementation constraints

- Keep the implementation mostly inline.
- Prefer understanding LangGraph primitives over reusable wrapper functions.
- Do not connect this simulated graph to the production API/CLI surface.
- Debug prints are intentionally kept for learning.
