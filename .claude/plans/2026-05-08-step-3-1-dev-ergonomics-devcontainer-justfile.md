# Step 3.1 — Dev ergonomics: devcontainer, justfile, DEVELOPMENT.md

## Context

Step 3 (PR #9) landed the runtime walking skeleton — a real FastAPI process you could `docker compose up`. But contributor onboarding was still "remember the long `uv run` commands" and there was no standardized dev environment. Three small additions to fix that:

1. A repo-level VS Code devcontainer so contributors can clone + reopen-in-container and have everything wired up.
2. A `justfile` so common workflows (`just sync`, `just check`, `just serve`, `just docker-build`) are short and discoverable.
3. A `docs/DEVELOPMENT.md` long-form guide that the README links to.

This PR is parallel to the kernel build — it doesn't unblock or depend on any kernel step. Pure dev ergonomics.

## Scope

- **`.devcontainer/Dockerfile`** — base on `mcr.microsoft.com/devcontainers/python:1-3.13-bookworm`, copy the `uv` binary in (mirrors `api/Dockerfile`), install `just` + `sqlite3`.
- **`.devcontainer/devcontainer.json`** — forwards port 8000, sets `CURFEW_ROOT_TOKEN=dev-token-not-secure` + `CURFEW_DB_PATH`, installs the ruff/mypy/just/python VS Code extensions, runs `uv sync --all-packages --group dev` + `pre-commit install` on create.
- **`justfile`** at repo root — recipes for sync, check, lint, format, typecheck, test, migrate, serve, docker-build, docker-run, clean.
- **`docs/DEVELOPMENT.md`** — local-dev guide covering devcontainer + host setup, workspace orientation, recipe reference, common gotchas.
- **`README.md`** — one-line link to `docs/DEVELOPMENT.md`.

## Notable design choices

- **Single repo-level devcontainer**, not per-component. The whole point of the uv workspace is one editable environment; multi-container would undo that.
- **`just` over `make`.** Modern command runner; UX wins (`just --list`, sane shell semantics, no tab-vs-spaces footgun) outweigh the one-time install cost. Mainstream-ish in modern Python tooling (uv/Astral projects use it). Discussed and chosen in pre-PR review.
- **Devcontainer sets a clearly-fake root token** (`dev-token-not-secure`) so `just serve` works out of the box. Anyone deploying with that as the actual prod token deserves what they get; the string is obvious enough that it's also a useful audit signal.

## Verification

1. `docker build -f api/Dockerfile -t curfew-core:dev .` succeeds; container starts, applies migration, serves `/v1/health` with `{"status":"ok","db":"ok"}`. (This was also the first time PR #9's Dockerfile actually got smoke-tested with the daemon running; it worked.)
2. `just --version` (1.50.0); `just --list` shows all 11 recipes.
3. `just check` runs ruff + ruff format + mypy + 46 tests, all green.
4. `.devcontainer/devcontainer.json` parses as JSON (after stripping JSONC comments).
5. Devcontainer image build deferred to first VS Code "Reopen in Container" — building it locally now isn't necessary for review.

## Branch + PR

- Branch: `dev-ergonomics` off `main`.
- One commit `8105bb3`. 5 files changed, 205 insertions.
- PR #10, merged.
