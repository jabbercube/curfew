FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY core ./core
COPY api ./api
COPY cli ./cli
COPY agents ./agents

RUN uv sync --frozen --no-dev --package curfew-api

EXPOSE 8000

# Apply migrations on startup, then serve. The DB lives at /var/lib/curfew/state.sqlite
# (compose.yml mounts a volume there); CURFEW_DB_PATH is set by the operator to match.
CMD ["sh", "-c", "cd api && uv run alembic upgrade head && cd .. && uv run uvicorn curfew_api.app:create_app --factory --host 0.0.0.0 --port 8000"]
