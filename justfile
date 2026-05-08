# Make bun (installed by https://bun.sh/install) discoverable for non-login
# shells that just spawns. The override is harmless when bun lives elsewhere
# (the directory just won't be found) and lets contributors use the standard
# installer without re-shimming PATH for `just`.
export PATH := env_var('HOME') / ".bun/bin:" + env_var('PATH')

# Pin CURFEW_DB_PATH to <repo>/state.sqlite by default so `migrate` (which
# cd's into api/) and `serve` (which runs from the repo root) agree on one
# file. The env_var_or_default honours an explicit CURFEW_DB_PATH so docker
# / CI / production deploys aren't disturbed. Tests set their own via
# monkeypatch and don't see this. The Docker image's ENV override is
# untouched (just doesn't pass this through to `docker run`).
export CURFEW_DB_PATH := env_var_or_default('CURFEW_DB_PATH', justfile_directory() / 'state.sqlite')

# Default CURFEW_ROOT_TOKEN to the same clearly-fake value the devcontainer
# uses (.devcontainer/devcontainer.json containerEnv) so a fresh host shell
# can `just serve` / `just user-create` without remembering to export it.
# env_var_or_default still honours an explicit CURFEW_ROOT_TOKEN, so docker
# / CI / production set their own and aren't disturbed. The token name
# screams "dev only" — anyone deploying with `dev-token-not-secure` as the
# real token deserves what they get; the string is also a useful audit
# signal if it ever leaks.
export CURFEW_ROOT_TOKEN := env_var_or_default('CURFEW_ROOT_TOKEN', 'dev-token-not-secure')

_default:
    @just --list

# Install all workspace deps + dev group, install pre-commit hooks. Also
# installs the JS deps for web/ so a fresh clone is fully bootstrapped.
sync: web-install
    uv sync --all-packages --group dev
    uv run pre-commit install

# Run lint + format check + typecheck + tests across both Python and web
check: lint typecheck test web-check

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

# Create a user via the root token. For first-run bootstrap and creating
# test users during dev. Requires `just serve` running and CURFEW_ROOT_TOKEN
# in the environment. Role defaults to admin so `just user-create alice
# test1234` makes you an admin to log in as.
#
# Examples:
#   just user-create alice test1234           # admin alice
#   just user-create kid1 kid12345 member     # managed member kid1
user-create username password role="admin" host="http://localhost:8000":
    # --fail-with-body (curl 7.76+) prints the response body AND exits non-zero
    # on 4xx/5xx, so the FastAPI 422 detail (e.g. "password too short") is
    # actually visible. Plain `-f` would swallow it.
    curl --fail-with-body -sS -X POST \
        -H "Authorization: Bearer $CURFEW_ROOT_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"username":"{{username}}","password":"{{password}}","role":"{{role}}"}' \
        {{host}}/v1/users

# Build the curfew-core Docker image. Builds the SPA first so the image
# bundles the latest UI; the Dockerfile copies web/dist into the image.
docker-build: web-build
    docker build -f api/Dockerfile -t curfew-core:dev .

# Run the curfew-core image (requires CURFEW_ROOT_TOKEN env on the host)
docker-run:
    docker run --rm -e CURFEW_ROOT_TOKEN -p 8000:8000 curfew-core:dev

# Remove the venv, caches, and any local SQLite state
clean:
    rm -rf .venv .mypy_cache .ruff_cache .pytest_cache
    find . -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    rm -f state.sqlite api/state.sqlite
    rm -rf web/node_modules web/dist web/coverage

# --- web/ recipes ------------------------------------------------------------
# Run inside web/ via `cd`. bun is the package manager; if you don't have it,
# install via https://bun.sh/install.

# Install JS deps
web-install:
    cd web && bun install

# Vite dev server on :5173 (proxies /v1 -> :8000; pair with `just serve`)
web-dev:
    cd web && bun run dev

# Production build (writes web/dist; consumed by api/Dockerfile)
web-build:
    cd web && bun run build

# tsc --noEmit
web-typecheck:
    cd web && bun run typecheck

# eslint
web-lint:
    cd web && bun run lint

# prettier --write
web-format:
    cd web && bun run format

# vitest run
web-test:
    cd web && bun run test

# Regenerate src/api/schema.ts from the live API (requires `just serve`)
web-schema:
    cd web && bun run schema

# Full web check: typecheck + lint + format-check + tests + build
web-check:
    cd web && bun run typecheck && bun run lint && bun run format:check && bun run test && bun run build
