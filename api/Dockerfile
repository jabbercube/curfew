FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY core ./core
COPY api ./api
COPY cli ./cli
COPY agents ./agents

# SPA bundle (built with `just web-build` outside the container, before
# `docker build`). Optional — if `web/dist` is missing, FastAPI's static
# mount is a no-op (check_dir=False) and the API still serves /v1/*.
COPY web/dist ./web/dist

RUN uv sync --frozen --no-dev --package curfew-api

EXPOSE 8000

# Default DB path inside the image. compose.yml overrides this to point at the
# mounted volume (/var/lib/curfew/state.sqlite). Setting it here ensures bare
# `docker run` works without forcing the operator to remember CURFEW_DB_PATH —
# alembic upgrade and uvicorn both see the same path.
ENV CURFEW_DB_PATH=/app/state.sqlite

# Apply migrations on startup, then serve. Alembic resolves script_location
# relative to its CWD, so we cd into api/ for the upgrade only.
CMD ["sh", "-c", "cd api && uv run alembic upgrade head && cd .. && uv run uvicorn curfew_api.app:create_app --factory --host 0.0.0.0 --port 8000"]
