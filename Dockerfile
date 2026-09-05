# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.14-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:latest

FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS runtime

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PATH="/app/.venv/bin:${PATH}" \
    PORT=8000 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini main.py ./
COPY alembic ./alembic
COPY my_agents ./my_agents
# Operator setup/status commands must be available in the deployed image.
COPY scripts ./scripts

EXPOSE 8000

CMD ["sh", "-c", "uv run --no-sync uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
