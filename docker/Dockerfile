FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml README.md uv.lock ./
COPY src ./src

RUN uv sync --frozen --no-dev

CMD ["uv", "run", "python", "-c", "print('curfew-core not yet implemented')"]
