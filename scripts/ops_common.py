"""Shared helpers for repository-local operator scripts."""

from __future__ import annotations

import argparse
from pathlib import Path

ENV_FILE_BY_PROFILE = {
    "pgvector.local": Path(".env.pgvector.local"),
    "pgvector.production": Path(".env.pgvector.production"),
}


def add_env_arguments(parser: argparse.ArgumentParser) -> None:
    """Add common safe env-profile arguments to an operator script parser."""
    parser.add_argument(
        "--env",
        choices=tuple(ENV_FILE_BY_PROFILE),
        default="pgvector.local",
        help=(
            "Named env file to load. Defaults to pgvector.local for safety; "
            "use pgvector.production only when intentionally operating on production."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Explicit env file path. Overrides --env when provided.",
    )


def resolve_env_file(*, profile: str, env_file: Path | None = None) -> Path:
    """Resolve an operator env file from a named profile or explicit path."""
    selected = env_file or ENV_FILE_BY_PROFILE[profile]
    return selected.expanduser()


def env_argv(*, profile: str, env_file: Path | None = None) -> list[str]:
    """Return argv tokens that preserve the selected env target for delegated scripts."""
    if env_file is not None:
        return ["--env-file", str(env_file)]
    return ["--env", profile]
