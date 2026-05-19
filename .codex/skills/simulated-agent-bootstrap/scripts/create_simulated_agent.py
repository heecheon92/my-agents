#!/usr/bin/env python3
# ruff: noqa: E501
"""Create a learning-only simulated-agent scaffold.

The scaffold follows this repository's simulated agent convention:

my_agents/simulated_agents/<agent_name>/
├── README.md
├── README.en.md
├── __init__.py
└── graph.py  # starter terminal loop for implementation practice
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def slugify(value: str) -> str:
    """Return a snake_case package name from a human-provided agent name."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        raise ValueError("agent name must contain at least one letter or digit")
    if slug[0].isdigit():
        slug = f"agent_{slug}"
    return slug


def titleize(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("_"))


def find_simulated_agents_root(start: Path) -> Path:
    """Find my_agents/simulated_agents from start or its parents."""
    for candidate in [start, *start.parents]:
        simulated_root = candidate / "my_agents" / "simulated_agents"
        if simulated_root.is_dir():
            return simulated_root
    raise FileNotFoundError(
        "Could not find my_agents/simulated_agents from the current directory. "
        "Run from the repository root or pass --simulated-root."
    )


def write_new_file(path: Path, content: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.write_text(content, encoding="utf-8")


def korean_readme(title: str, slug: str) -> str:
    return f"""# {title} simulated agent

[English](./README.en.md)

이 폴더는 **TODO: 연습할 LangGraph 패턴 이름** 패턴을 연습하기 위한 에이전트 개발 랩입니다.

`graph.py`에는 현재 터미널 입력을 받는 bootstrap `while True` 루프가 들어 있습니다. 구현 목적은 프로덕션 품질보다 요구사항을 LangGraph 노드, 상태, 라우팅, 종료 조건으로 직접 번역하는 연습입니다.

## 연습할 패턴

```text
User
  ↓
TODO: first node
  ↓
TODO: next node or route
  ↓
END
```

이 패턴의 핵심은 TODO: 이 simulated agent가 연습하려는 LangGraph 개념을 한두 문장으로 설명합니다.

- **TODO: Node 1**: 이 노드가 상태에서 무엇을 읽고 무엇을 저장하는지 설명합니다.
- **TODO: Node 2**: 이 노드가 어떤 결정을 하거나 어떤 출력을 만드는지 설명합니다.
- **Route function**: 어떤 상태 필드를 보고 다음 노드 또는 종료를 결정하는지 설명합니다.

## 에이전트 목표

사용자가 TODO: 어떤 입력을 주면, {title}는 TODO: 어떤 상태 전환과 결과를 만들어야 합니다.

예시 입력:

```text
TODO: example user request
```

## 요구 동작

### 1. TODO first node

이 노드는 사용자에게 직접 최종 답변하지 않습니다.

이 노드는 TODO: structured output 또는 plain value를 만들고 상태의 `TODO_state_field`에 저장합니다.

```python
{{
    "TODO_state_field": "TODO example value",
}}
```

이 노드의 책임:

- TODO: 입력에서 필요한 정보를 찾습니다.
- TODO: 다음 노드가 사용할 상태를 만듭니다.
- TODO: fake/stub 경계가 있다면 명확히 표시합니다.

### 2. TODO second node

이 노드는 이전 노드가 만든 상태를 바탕으로 TODO: 실제 작업 또는 판단을 수행합니다.

출력에는 다음이 있어야 합니다.

- TODO: 필수 출력 1
- TODO: 필수 출력 2
- TODO: 다음 노드 또는 사용자에게 전달할 정보

### 3. TODO review/route/final node

이 노드는 TODO: 결과를 검토하거나, 다음 경로를 선택하거나, 최종 답변을 만듭니다.

승인/종료 기준:

- TODO: 기준 1
- TODO: 기준 2
- TODO: 기준 3

## 라우팅 / 반복 규칙

TODO: 그래프가 언제 종료하고 언제 이전 노드로 돌아가는지 적습니다.

무한 루프를 막아야 하는 패턴이라면 최대 반복 횟수를 명시합니다.

```python
if TODO_condition:
    return "END"

if revision_count >= 2:
    return "END"

return "TODO_next_node"
```

## 상태 설계

그래프 전체의 공유 상태 이름은 구체적인 도메인 이름을 사용합니다. 예: `{title.replace(" ", "")}State`.

```python
class {title.replace(" ", "")}State(TypedDict, total=False):
    user_request: str
    TODO_state_field: str
    TODO_decision: str
    final_result: str
```

이름을 `AgentState`처럼 일반적으로 짓기보다, 이 그래프 전체가 공유하는 노트북이라는 점이 드러나게 이름 붙입니다.

## 그래프 초안

```mermaid
flowchart TD
    Start([START]) --> First["TODO: first node"]
    First --> Second["TODO: second node"]
    Second --> Route{{"TODO: route decision"}}
    Route -->|done| End([END])
    Route -->|continue| Second
```

## 실행 방법

현재 bootstrap은 OpenAI API 키 없이 실행됩니다.

```bash
uv run python -m my_agents.simulated_agents.{slug}.graph
```

종료:

```text
/exit
```

구현 후에는 학습을 위해 각 노드에서 다음처럼 debug 로그를 남기는 것을 권장합니다.

```text
[node_name] what this node is doing
[route] deciding next node
[final result]
```

## 학습 포인트

이 그래프는 TODO: 기존 simulated agent와 비교해서 어떤 새 패턴을 연습하는지 설명합니다.

- TODO: 기존 예제와의 차이
- TODO: 이 패턴이 실제 agent 시스템에서 쓰이는 상황
- TODO: 구현 중 특히 주의할 LangGraph primitive

## 구현 메모

- 가능한 한 inline 코드로 구현합니다.
- 재사용 가능한 wrapper 함수보다 LangGraph primitive 이해를 우선합니다.
- 프로덕션 API/CLI surface에 연결하지 마세요.
- 실제 외부 side effect 대신 fake/stub boundary를 우선하세요.
- debug print는 학습을 위해 의도적으로 남겨둘 수 있습니다.
"""


def english_readme(title: str, slug: str) -> str:
    return f"""# {title} simulated agent

[한국어](./README.md) | English

This folder is an agent development lab for practicing the **TODO: LangGraph pattern name** pattern.

`graph.py` currently contains a bootstrap terminal `while True` loop. The goal is not production polish; the goal is to practice translating requirements into LangGraph nodes, state, routing, and stop conditions.

## Pattern to practice

```text
User
  ↓
TODO: first node
  ↓
TODO: next node or route
  ↓
END
```

The core idea of this pattern is TODO: explain the LangGraph concept this simulated agent practices in one or two sentences.

- **TODO: Node 1**: explain what this node reads from state and what it stores.
- **TODO: Node 2**: explain what decision it makes or what output it creates.
- **Route function**: explain which state fields decide the next node or stop condition.

## Agent goal

When the user provides TODO: input shape, {title} should TODO: describe the expected state transitions and result.

Example input:

```text
TODO: example user request
```

## Required behavior

### 1. TODO first node

This node does not directly produce the final user answer.

It creates TODO: structured output or plain value and stores it in `TODO_state_field` state.

```python
{{
    "TODO_state_field": "TODO example value",
}}
```

This node's responsibilities:

- TODO: identify the needed information from input.
- TODO: create state that the next node can use.
- TODO: clearly mark fake/stub boundaries if any.

### 2. TODO second node

This node uses the previous node's state to TODO: perform work or make a decision.

The output should include:

- TODO: required output 1
- TODO: required output 2
- TODO: information for the next node or user

### 3. TODO review/route/final node

This node TODO: reviews the result, chooses the next route, or creates the final answer.

Approval/stop criteria:

- TODO: criterion 1
- TODO: criterion 2
- TODO: criterion 3

## Routing / loop rule

TODO: describe when the graph ends and when it returns to an earlier node.

If the pattern can loop forever, specify a maximum iteration count.

```python
if TODO_condition:
    return "END"

if revision_count >= 2:
    return "END"

return "TODO_next_node"
```

## State design

Use a concrete domain name for shared graph state. Example: `{title.replace(" ", "")}State`.

```python
class {title.replace(" ", "")}State(TypedDict, total=False):
    user_request: str
    TODO_state_field: str
    TODO_decision: str
    final_result: str
```

Prefer a specific name over `AgentState` so it is clear that the state belongs to the whole graph, not to one agent/node.

## Draft graph

```mermaid
flowchart TD
    Start([START]) --> First["TODO: first node"]
    First --> Second["TODO: second node"]
    Second --> Route{{"TODO: route decision"}}
    Route -->|done| End([END])
    Route -->|continue| Second
```

## How to run

The current bootstrap runs without an OpenAI API key.

```bash
uv run python -m my_agents.simulated_agents.{slug}.graph
```

Exit with:

```text
/exit
```

After implementation, prefer node-level debug logs for learning:

```text
[node_name] what this node is doing
[route] deciding next node
[final result]
```

## Learning points

This graph practices TODO: explain what new pattern this adds compared with existing simulated agents.

- TODO: difference from an existing example
- TODO: where this pattern appears in real agent systems
- TODO: LangGraph primitive to pay attention to

## Implementation notes

- Keep the implementation mostly inline.
- Prefer understanding LangGraph primitives over reusable wrapper functions.
- Do not connect this simulation to production API/CLI surfaces.
- Prefer fake/stub boundaries over real external side effects.
- Debug prints may intentionally remain for learning.
"""


def init_py(slug: str) -> str:
    return f'"""Learning-only simulated agent package: {slug}."""\n'


def graph_py(slug: str, title: str) -> str:
    return f'''"""Bootstrap graph module for the {title} simulated agent.

This file intentionally starts with only a terminal loop. Replace `respond()` with
LangGraph state, nodes, routing, and graph invocation after the learning pattern is
clear.
"""

from __future__ import annotations

AGENT_NAME = "{slug}"


def respond(user_input: str) -> str:
    """Return a placeholder response until the LangGraph pattern is implemented."""
    return (
        f"[{{AGENT_NAME}} bootstrap] TODO: replace respond() with your graph.invoke(...) "
        f"call. Received: {{user_input}}"
    )


if __name__ == "__main__":
    while True:
        try:
            user_input = input("🧑‍💻 User: ")
            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                break

            print(respond(user_input))
        except KeyboardInterrupt:
            print("\\nGoodbye!")
            break
        except Exception as exc:
            print(f"{{type(exc).__name__}}: {{exc}}")
            break
'''


def create_scaffold(agent_name: str, simulated_root: Path, *, overwrite: bool) -> Path:
    slug = slugify(agent_name)
    title = titleize(slug)
    agent_dir = simulated_root / slug
    agent_dir.mkdir(parents=True, exist_ok=True)

    write_new_file(agent_dir / "README.md", korean_readme(title, slug), overwrite=overwrite)
    write_new_file(agent_dir / "README.en.md", english_readme(title, slug), overwrite=overwrite)
    write_new_file(agent_dir / "__init__.py", init_py(slug), overwrite=overwrite)
    write_new_file(agent_dir / "graph.py", graph_py(slug, title), overwrite=overwrite)
    return agent_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a simulated agent bootstrap folder.")
    parser.add_argument("agent_name", help="New simulated agent name, normalized to snake_case.")
    parser.add_argument(
        "--simulated-root",
        type=Path,
        help="Path to my_agents/simulated_agents. Defaults to auto-detecting from cwd.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing scaffold files. Use only when intentionally regenerating a bootstrap.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        simulated_root = args.simulated_root or find_simulated_agents_root(Path.cwd())
        simulated_root = simulated_root.resolve()
        agent_dir = create_scaffold(args.agent_name, simulated_root, overwrite=args.overwrite)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(agent_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
