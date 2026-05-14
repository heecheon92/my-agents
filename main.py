"""ASGI entrypoint for the my-agents backend."""

from my_agents.api import create_app

app = create_app()
