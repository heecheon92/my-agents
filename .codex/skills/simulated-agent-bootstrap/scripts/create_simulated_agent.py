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


def korean_readme(title: str) -> str:
    return f"""# {title} simulated agent

[English](./README.en.md)

이 폴더는 **학습 전용 simulated agent**를 만들기 위한 부트스트랩 공간입니다.

`graph.py`에는 `mbti`와 `study_coach` 예제처럼 터미널 입력을 받는 `while True` 루프가 들어 있습니다. 먼저 그래프 패턴, 상태, 노드, 라우팅 규칙을 설계한 뒤 `respond()`를 실제 LangGraph 호출로 바꾸세요.

## 목표

- 연습할 LangGraph 패턴: TODO
- 사용자 입력 예시: TODO
- 기대 출력 또는 동작: TODO

## 그래프 초안

```mermaid
flowchart TD
    Start([START]) --> First["TODO: first node"]
    First --> End([END])
```

## 파일 책임

| 파일 | 책임 |
| --- | --- |
| `graph.py` | terminal `while True` 루프와 `respond()` placeholder가 있는 simulated agent 구현 시작점 |
| `README.md` | 한국어 학습 노트와 구현 계획 |
| `README.en.md` | English learning note and implementation plan |
| `__init__.py` | simulation package marker |

## 구현 메모

- 프로덕션 API/CLI surface에 연결하지 마세요.
- 실제 외부 side effect 대신 fake/stub boundary를 우선하세요.
- 구현 후 이 README에 그래프 흐름, 핵심 상태 필드, fake/simulation 경계를 업데이트하세요.
"""


def english_readme(title: str) -> str:
    return f"""# {title} simulated agent

[한국어](./README.md) | English

This folder is a bootstrap space for a **learning-only simulated agent**.

`graph.py` includes a terminal `while True` loop like the `mbti` and `study_coach` examples. Design the graph pattern, state, nodes, and routing rules before replacing `respond()` with a real LangGraph invocation.

## Goal

- LangGraph pattern to practice: TODO
- Example user input: TODO
- Expected output or behavior: TODO

## Draft graph

```mermaid
flowchart TD
    Start([START]) --> First["TODO: first node"]
    First --> End([END])
```

## File responsibilities

| File | Responsibility |
| --- | --- |
| `graph.py` | Simulated agent starting point with a terminal `while True` loop and `respond()` placeholder |
| `README.md` | Korean learning note and implementation plan |
| `README.en.md` | English learning note and implementation plan |
| `__init__.py` | Simulation package marker |

## Implementation notes

- Do not connect this simulation to production API/CLI surfaces.
- Prefer fake/stub boundaries over real external side effects.
- After implementation, update this README with graph flow, key state fields, and fake/simulation boundaries.
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

    write_new_file(agent_dir / "README.md", korean_readme(title), overwrite=overwrite)
    write_new_file(agent_dir / "README.en.md", english_readme(title), overwrite=overwrite)
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
