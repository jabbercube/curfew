# Step 1 — Repository skeleton

## Context

The repo is in pure planning state — `docs/`, `README.md`, and `.claude/` only. PLAN.md commits to a kernel-first build: build a complete kernel (storage, FastAPI core, two SDKs, CLI, reference test consumers, end-to-end acceptance) before any feature work, in any internal order.

Step 1 lays down the **repository skeleton** that every subsequent kernel slice will commit into: the directory tree from PLAN.md §"Repository layout", a single root `pyproject.toml` configuring lint/type/test, pre-commit, stub Dockerfile + compose, and a green Linux CI workflow running `ruff + mypy + pytest` on an empty test suite. **No functional code.** The point is to make every later commit land in a structured, lint-passing, CI-green repo from line 1.

Why this first: it is small, has unambiguous done criteria (CI green), is dictated almost entirely by PLAN.md (so design surface is near-zero), and unblocks every other slice. Storage models, the FastAPI app, the SDKs, the CLI, and the reference test consumers all want to land into the directory tree this step creates.

## Scope confirmed with user

- **Skeleton only** — no SQLModel models, no FastAPI app, no Alembic migrations yet.
- **src-layout, multi-package**, exactly as PLAN.md §"Repository layout" describes.
- One `pyproject.toml` at root for now (all `src/*` packages discoverable from one install). Per-package `pyproject.toml` split is deferred until the first agent SDK actually ships and needs independent publishing.

## Tooling choices

| Concern | Choice | Why |
|---|---|---|
| Build backend | `hatchling` | PyPA-standard, lightweight, no opinion on installer. |
| Installer | `pip` (CI) / `uv` (local, optional) | Standard install path; `uv` works against the same `pyproject.toml` if a contributor wants it. |
| Python floor | `>=3.12` | FastAPI + Pydantic v2 + SQLModel all support it; modern type syntax. CI on 3.13. |
| Linter + formatter | `ruff` | PLAN.md §"Testing strategy" specifies `ruff + mypy + pytest`. |
| Type checker | `mypy --strict` for `src/curfew/` and `src/curfew_api/` | PLAN.md: "`mypy --strict` for the core". The SDKs and CLI start non-strict; tighten later. |
| Test framework | `pytest` | PLAN.md specifies. |
| Git hooks | `pre-commit` framework, **installed at the `pre-push` stage** (not commit stage) | Run lint/type checks once before pushing instead of on every WIP commit. PLAN.md says "Pre-commit hooks for lint/format" — same tool, different stage. Configured via `default_install_hook_types: [pre-push]` in `.pre-commit-config.yaml`; contributors run `pre-commit install` once. |
| CI | One GitHub Actions workflow (`.github/workflows/ci.yml`) | The Windows workflow (`windows.yml` per PLAN.md) is **deferred** until PowerShell code exists — there's nothing to test on Windows yet. Add a second workflow when the PowerShell SDK lands. |

## Files to create

### Root

- `pyproject.toml` — hatchling build backend, packages discovered from `src/` (`curfew`, `curfew_api`, `curfew_cli`, `curfew_agent_sdk_python`), Python `>=3.12`, dev-dep group `[ruff, mypy, pytest, pytest-asyncio, pre-commit, types-*]`, runtime deps left empty for step 1, `[tool.ruff]` + `[tool.mypy]` + `[tool.pytest.ini_options]` blocks.
- `.gitignore` — append standard Python ignores (`__pycache__/`, `*.pyc`, `.venv/`, `dist/`, `*.egg-info/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `.coverage`, `htmlcov/`). Preserve current content (currently a blank line).
- `.pre-commit-config.yaml` — ruff (lint + format) + mypy hooks, with `default_install_hook_types: [pre-push]` so `pre-commit install` wires them as pre-**push** hooks. (Despite the framework's name, it supports any git hook stage; "pre-commit" is the package, "pre-push" is the stage we install at.)
- `.python-version` — `3.13` (developer hint; CI is authoritative).

### Source tree (each package gets `__init__.py` only — empty packages, no functional code)

- `src/curfew/__init__.py` — shared library: future home of models, schemas, rule interface, `Plugin` base class.
- `src/curfew_api/__init__.py` — FastAPI service.
- `src/curfew_api/migrations/.gitkeep` — Alembic dir, populated in the storage step.
- `src/curfew_cli/__init__.py` — CLI client.
- `src/curfew_agent_sdk_python/__init__.py` — Python agent SDK.
- `src/curfew-agent-sdk-powershell/.gitkeep` — PowerShell module, populated when the PowerShell SDK lands. Note in a sibling `README.md` that this is **not** a Python package despite living under `src/`.

### Empty-but-tracked dirs (with `.gitkeep`)

Mirroring PLAN.md §"Repository layout":

- `agents/windows-agent/.gitkeep`, `agents/macos-agent/.gitkeep`
- `plugins/.gitkeep` — default entry in `CURFEW_PLUGINS_DIRS`; populated when the first plugin (or `reftest_plugin`) lands.
- `tests/unit/.gitkeep`, `tests/api/.gitkeep`, `tests/cli/.gitkeep`, `tests/reftest_agent/.gitkeep`, `tests/reftest_plugin/.gitkeep`, `tests/e2e/.gitkeep`
- `tests/conftest.py` — empty; lets pytest discover the dir.

### Docker

- `docker/Dockerfile` — multi-stage stub: base on `python:3.13-slim`, install the project via `pip install -e .`, set `CMD` to a placeholder that prints "curfew-core not yet implemented" and exits 0. Replaced when the FastAPI app lands.
- `docker/compose.yml` — single `curfew-core` service, mounts `./state:/var/lib/curfew` for the future SQLite volume, exposes nothing yet, no Traefik wiring (added later).

### CI

- `.github/workflows/ci.yml` — on push + PR: checkout, setup Python 3.13, `pip install -e .[dev]`, `ruff check`, `ruff format --check`, `mypy src/curfew src/curfew_api`, `pytest`. Pytest runs against `tests/` which is empty — passes with "no tests collected" (use `--exitfirst` not required; default exit 0 on no tests once `[tool.pytest.ini_options]` sets `testpaths = ["tests"]` and pytest is configured to allow empty collection via `# no special flag — pytest exits 5 on no tests; we add a single trivial test in tests/unit/test_skeleton.py asserting True so collection is non-empty`).

### Trivial smoke test

- `tests/unit/test_skeleton.py` — one test: `def test_packages_import(): import curfew, curfew_api, curfew_cli, curfew_agent_sdk_python`. Confirms the install + imports work and gives pytest something to collect.

## Files to modify

- `.gitignore` — append Python ignores (preserving the empty current content).
- `README.md` — leave alone; it already points at the docs.

## What this step explicitly does **not** do

- No SQLModel models, no Alembic env, no migrations.
- No FastAPI app — `curfew_api/__init__.py` is empty; no `main.py` yet.
- No CLI commands — `curfew_cli/__init__.py` is empty; no Typer/argparse yet.
- No agent SDK code, no plugin SDK code, no `Plugin` base class.
- No Windows CI workflow (deferred to the step that introduces the PowerShell SDK).
- No Traefik wiring in `compose.yml` (deferred to the step that exposes a real port).
- No reference test consumers (`reftest_agent`, `reftest_plugin`).

## Verification

End-to-end checks the implementer should run (and CI must pass) before declaring step 1 done:

1. `pip install -e .[dev]` — installs cleanly in a fresh venv.
2. `python -c "import curfew, curfew_api, curfew_cli, curfew_agent_sdk_python"` — all four packages importable.
3. `ruff check .` — clean.
4. `ruff format --check .` — clean.
5. `mypy src/curfew src/curfew_api` — clean (passes vacuously on empty packages with `--strict`).
6. `pytest` — passes (one trivial test).
7. `pre-commit install` (one-time) wires the hooks at the `pre-push` stage (not commit stage); `pre-commit run --all-files` — clean. A `git commit` does **not** trigger the hooks; a `git push` does.
8. `docker build -f docker/Dockerfile .` — builds, image runs, prints the placeholder, exits 0.
9. Push to a branch / open PR → GitHub Actions `ci.yml` workflow goes green.

## Critical files for the implementer to reference

- `docs/PLAN.md` §"Repository layout" — authoritative directory tree.
- `docs/PLAN.md` §"Testing strategy" — confirms `ruff + mypy + pytest` stack and the eventual Windows runner.
- `docs/PLAN.md` §"Configuration and settings" — informs (but doesn't yet drive) future `BaseSettings` work.
- `docs/DECISIONS.md` ADR-002 — SQLite + SQLModel + Alembic commitment that step 2 will land.
- `.gitignore` (currently empty) — preserve and extend.

## What step 2 looks like (for context only — not part of this step)

Storage kernel: SQLModel models for every kernel table in PLAN.md §"Tables" + initial Alembic migration creating them all + the single `settings` row with defaults. Lands in `src/curfew/models.py` (or split files) and `src/curfew_api/migrations/`. Step 1's `mypy --strict` config and pre-commit hooks make this land cleanly without churn.
