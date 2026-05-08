# Development

How to set up curfew for local development. Two paths: the bundled devcontainer (zero local setup) or installing the toolchain on your host.

## Devcontainer (recommended)

The repo ships a [devcontainer](../.devcontainer/) configured with Python 3.13, [uv](https://github.com/astral-sh/uv), [just](https://github.com/casey/just), `sqlite3`, and the VS Code extensions for ruff and mypy.

1. Open the repo in VS Code.
2. Run **"Dev Containers: Reopen in Container"** from the command palette.
3. The first build takes a minute; subsequent opens are instant.
4. The container's `postCreateCommand` runs `uv sync --all-packages --group dev` and installs pre-commit hooks. You're ready to go.

The devcontainer sets `CURFEW_ROOT_TOKEN=dev-token-not-secure` and `CURFEW_DB_PATH=/workspaces/curfew/state.sqlite` so `just serve` works without further setup. Don't reuse that token for any deployment that's reachable from outside your machine.

## Local setup (without the devcontainer)

You need:

- Python 3.13
- [uv](https://docs.astral.sh/uv/) for dep + workspace management
- [just](https://github.com/casey/just) as the command runner
- `sqlite3` CLI for inspecting state
- (Optional) Docker, if you want to build the curfew-core image locally

```sh
brew install uv just sqlite                       # macOS
# apt install just sqlite3 (Debian/Ubuntu) + uv via the official installer
# winget install Casey.Just (Windows) + uv via the official installer

just sync                                         # install workspace + pre-commit hooks
just check                                        # ruff + ruff format + mypy + pytest
CURFEW_ROOT_TOKEN=secret just serve               # uvicorn on http://127.0.0.1:8000
```

## `just` recipes

`just --list` shows all available recipes. The common ones:

| Recipe | What it does |
|---|---|
| `just sync` | `uv sync --all-packages --group dev` + `pre-commit install` |
| `just check` | `lint` + `typecheck` + `test` (the full pre-push battery) |
| `just lint` | `ruff check .` + `ruff format --check .` |
| `just format` | `ruff check . --fix` + `ruff format .` (in-place) |
| `just typecheck` | `mypy` against the strict packages (`curfew`, `curfew_api`) |
| `just test` (`just test -k name` etc.) | `pytest` with optional args passed through |
| `just migrate` | `cd api && alembic upgrade head` against the configured DB |
| `just serve` | `migrate` then `uvicorn` on port 8000. Requires `CURFEW_ROOT_TOKEN`. |
| `just docker-build` | Build the curfew-core image locally |
| `just docker-run` | Run the curfew-core image (passes `CURFEW_ROOT_TOKEN` from your shell) |
| `just clean` | Remove `.venv`, caches, and any local SQLite files |

## Workspace layout (orientation)

Per [ADR-014](DECISIONS.md), the repo is a uv workspace with each top-level deliverable as its own member. Quick map:

- `core/` — shared library (`curfew` package): models, config, db, rule pipeline, Plugin base.
- `api/` — FastAPI service: `curfew_api` package, `Dockerfile`, Alembic migrations.
- `cli/` — operator CLI (`curfew_cli` package, thin HTTP client).
- `agents/sdk-python/` — Python agent SDK (`curfew_agent_sdk` package).
- `agents/sdk-powershell/` — PowerShell agent SDK module.
- `agents/<name>/` — concrete on-device agents (`windows-agent`, `macos-agent`, `reftest-agent`).
- `plugins/<name>/` — drop-in in-core plugins (the default `CURFEW_PLUGINS_DIRS` entry).
- `e2e/` — cross-component integration tests.

A single `uv sync --all-packages --group dev` from the repo root installs every member in editable mode and a unified `uv.lock` records every transitive dep.

## Running tests

```sh
just test                          # all tests
just test core/                    # only core/
just test -k test_health           # match by name
uv run pytest core/tests/test_models.py::test_user_round_trip  # one test
```

The test suite is fast (sub-second locally). Per-component tests live next to their code (`core/tests/`, `api/tests/`, …); cross-component integration goes in `e2e/`.

## Linting and type-checking

- `ruff check` covers lint; `ruff format` covers formatting (replaces black + isort).
- `mypy --strict` runs against the `curfew` and `curfew_api` packages. The CLI and agent SDKs are not yet under strict mode; tighten when their code lands.

Both run on `git push` via pre-commit (configured at the `pre-push` stage, not commit, per the project's preference for not slowing every WIP commit).

## Database

Default DB path is `state.sqlite` in the working directory. Override with `CURFEW_DB_PATH=/path/to/file.sqlite`. The Alembic env (`api/migrations/env.py`) honours the same env var.

To inspect:

```sh
sqlite3 state.sqlite '.schema'
sqlite3 state.sqlite 'SELECT * FROM settings;'
```

## Common gotchas

- **`just serve` fails with "ValidationError"** — `CURFEW_ROOT_TOKEN` is required and has no default. Set it in your shell or your `.env`.
- **`alembic upgrade head` fails with "Path doesn't exist: migrations"** — Alembic resolves `script_location` relative to the working directory. Run from `api/`, not the repo root.
- **`pytest` collects 0 tests after a failed run** — leftover `__pycache__` directories may shadow renamed test files. `just clean` and re-run.
- **`mypy` complains about a missing `py.typed`** — every Python package in the workspace ships a `py.typed` marker. If you add a new package, add the marker too.
