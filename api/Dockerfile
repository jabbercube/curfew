FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY core ./core
COPY api ./api
COPY cli ./cli
COPY agents ./agents

RUN uv sync --frozen --no-dev --package curfew-api

CMD ["uv", "run", "--package", "curfew-api", "python", "-c", "print('curfew-core not yet implemented')"]
