_default:
    @just --list

# Install all workspace deps + dev group, install pre-commit hooks
sync:
    uv sync --all-packages --group dev
    uv run pre-commit install

# Run lint + format check + typecheck + tests
check: lint typecheck test

# Lint + format check (no fixes)
lint:
    uv run ruff check .
    uv run ruff format --check .

# Apply lint fixes + format in place
format:
    uv run ruff check . --fix
    uv run ruff format .

# Type-check the strict packages
typecheck:
    uv run mypy

# Run the full test suite
test *args:
    uv run pytest {{args}}

# Apply migrations against the configured DB (CURFEW_DB_PATH or default)
migrate:
    cd api && uv run alembic upgrade head

# Serve curfew-core locally on port 8000 (requires CURFEW_ROOT_TOKEN env)
serve: migrate
    uv run uvicorn curfew_api.app:create_app --factory --host 127.0.0.1 --port 8000

# Build the curfew-core Docker image
docker-build:
    docker build -f api/Dockerfile -t curfew-core:dev .

# Run the curfew-core image (requires CURFEW_ROOT_TOKEN env on the host)
docker-run:
    docker run --rm -e CURFEW_ROOT_TOKEN -p 8000:8000 curfew-core:dev

# Remove the venv, caches, and any local SQLite state
clean:
    rm -rf .venv .mypy_cache .ruff_cache .pytest_cache
    find . -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    rm -f state.sqlite api/state.sqlite
