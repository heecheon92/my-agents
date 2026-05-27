"""CLI wrapper for the document ingestion worker."""

from __future__ import annotations

from my_agents.knowledge.ingestion_worker import main

if __name__ == "__main__":
    raise SystemExit(main())
