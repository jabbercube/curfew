# Step 3 — Runtime walking skeleton

## Context

The storage kernel (PR #5, #6, #7, #8) defined every kernel table and a working migration. Nothing yet *runs*: there is no FastAPI process, no engine factory, no auth, no health endpoint. A `docker compose up` lands the placeholder `CMD ["uv", "run", "python", "-c", "print('curfew-core not yet implemented')"]` and exits.

This step lands the runtime side of the kernel as a **walking skeleton**: a real FastAPI service that boots from config, opens the database, serves `/v1/health` unauthenticated, and protects everything else with the root bearer token. After this PR, `docker compose up` produces a service responding to HTTP.

Audit middleware, the rule pipeline, and the first CRUD endpoints land in the next PR (the first vertical slice). Reasoning: audit middleware is easier to write *with* a real write endpoint to audit; the rule pipeline benefits from designing alongside its first concrete rule (`manual_lock`). Bundling all three into one PR mixes three reviewable concerns. This PR is the smallest unit that produces a deployable, testable service.

## Scope

**In:**

1. **Config loader** — `core/curfew/config.py`. `pydantic-settings` `BaseSettings` reading env vars per PLAN.md §"Configuration and settings". Precedence: env > `.env` > `config.json` > defaults. Schema-validated at boot; bad config fails fast.
2. **DB engine + session dependency** — `core/curfew/db.py`. Sync SQLAlchemy engine factory pointed at `CURFEW_DB_PATH`. Connection events: `PRAGMA foreign_keys = ON`, `PRAGMA journal_mode = WAL`. `get_session()` is a FastAPI dependency yielding a `Session`.
3. **FastAPI app factory** — `api/curfew_api/app.py` with `create_app() -> FastAPI`. OpenAPI at `/v1/openapi.json`. CORS configured from `CURFEW_CORS_ORIGINS`.
4. **Health endpoint** — `api/curfew_api/routes/health.py`. `GET /v1/health` returns `{status: "ok", db: "ok"}` (the `db` check is a `SELECT 1` round-trip). Unauthenticated.
5. **Auth middleware** — `api/curfew_api/auth.py`. Validates `Authorization: Bearer <CURFEW_ROOT_TOKEN>` for every request to `/v1/*` *except* `/v1/health` and `/v1/openapi.json`. Attaches `request.state.actor = "operator"` on success; 401 on missing/wrong. The `actor` attribute is the slot per-user-role auth (a future feature) extends without rename.
6. **Wire the Dockerfile** — replace the placeholder `CMD` with `uv run uvicorn curfew_api.app:create_app --factory --host 0.0.0.0 --port 8000`. Apply the migration on startup via a small entrypoint script (or a one-shot `alembic upgrade head` in `CMD`'s lead).
7. **Tests** — `api/tests/test_app.py`: health unauthed 200; protected stub endpoint 401 without token, 200 with right token, 401 with wrong token; OpenAPI accessible without auth. `core/tests/test_config.py`: defaults, env override, `.env` override, `config.json` override, bad value fails fast, secrets never read from `config.json`.
8. **Deps** — `api/pyproject.toml`: `fastapi`, `uvicorn[standard]`, `httpx` (for tests via `TestClient`). `core/pyproject.toml`: `pydantic-settings`.

**Out (next PR — first vertical slice):**

- Audit middleware
- Rule pipeline + `manual_lock` rule
- `POST /v1/users/{user}/lock`, `POST /v1/users/{user}/unlock`
- `GET /v1/users/{user}/status`, `GET /v1/devices/{device}/status`
- The first user/device CRUD endpoints (needed to seed state for the lock flow)

## Files to create / modify

### New

- `core/curfew/config.py` — `Settings` BaseSettings class with all `CURFEW_*` env vars; `get_config()` cache.
- `core/curfew/db.py` — `make_engine(config)`, `get_session()` dependency, connection-event hooks.
- `api/curfew_api/app.py` — `create_app()` factory mounting middleware + routers.
- `api/curfew_api/auth.py` — bearer middleware; `Annotated[str, Depends(require_operator)]` dependency for protected routes.
- `api/curfew_api/routes/__init__.py` — empty package.
- `api/curfew_api/routes/health.py` — `/v1/health` route + `db: "ok"` round-trip.
- `api/tests/test_app.py` — health, auth, OpenAPI tests via `fastapi.testclient.TestClient`.
- `core/tests/test_config.py` — config precedence + validation tests.

### Modified

- `core/pyproject.toml` — add `pydantic-settings>=2.5`.
- `api/pyproject.toml` — add `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `httpx>=0.27`.
- `api/Dockerfile` — replace placeholder `CMD` with uvicorn invocation; ensure migrations run before serving.
- `core/curfew/__init__.py` — re-export `Settings`, `get_config`, `make_engine`, `get_session`.
- `uv.lock` — regenerated.

## Implementation notes

- **Config precedence and `BaseSettings`:** `pydantic-settings` reads env first, then a single `_env_file` (set to `.env`). For `config.json` overlay (third tier per PLAN.md), use a `model_config = SettingsConfigDict(json_file="config.json", json_file_encoding="utf-8")` if available, otherwise a `pyproject_toml_settings_source` style customization. Concretely: define `Settings` with all fields and defaults, layer sources in `customise_sources`. Secrets (`CURFEW_ROOT_TOKEN`) are env-only — `Settings` raises if `config.json` defines it.
- **Auth middleware shape:** prefer a FastAPI dependency (`require_operator()`) attached to a router-level `dependencies=[Depends(require_operator)]` rather than ASGI middleware. This keeps the OpenAPI schema honest (FastAPI documents the security requirement) and makes the unauthed-allowlist (`/v1/health`, `/v1/openapi.json`, `/docs`) just "routes that don't include the dependency". Cleaner than path-prefix matching in middleware.
- **Health endpoint `db: "ok"`:** if the `SELECT 1` raises, return `{status: "degraded", db: "<error class>"}` with HTTP 200 (the process is up; the DB isn't). Operators that want a strict liveness probe use `/v1/health` and check `status`. Future: split into `/v1/health/live` and `/v1/health/ready` if container orchestration needs it.
- **Migrations on startup:** in the Docker image, `CMD ["sh", "-c", "uv run alembic -c api/alembic.ini upgrade head && uv run uvicorn ..."]` is the simplest path. For dev, the operator runs `alembic upgrade head` themselves; the app doesn't auto-migrate. Document either way.
- **`pydantic-settings` lives in `core/`** because both the API service and (eventually) the CLI may want to read shared config (e.g., the agent SDK reads `CURFEW_AGENT_BASE_URL` for default API URL).

## Verification

1. `uv sync --all-packages --group dev` — clean.
2. `uv run python -c "from curfew_api.app import create_app; app = create_app(); print('app created')"` — boots without DB if config doesn't require one. (Or: requires `CURFEW_ROOT_TOKEN`; verify the boot-time validation message is helpful when missing.)
3. `uv run ruff check . && uv run ruff format --check .` — clean.
4. `uv run mypy` — clean.
5. `uv run pytest` — all pre-existing 31 tests still pass; new `test_app.py` and `test_config.py` add ~10 more.
6. `uv run pre-commit run --all-files` — clean.
7. **End-to-end smoke:** in one terminal, `cd api && CURFEW_DB_PATH=/tmp/curfew.sqlite CURFEW_ROOT_TOKEN=secret uv run alembic upgrade head && CURFEW_DB_PATH=/tmp/curfew.sqlite CURFEW_ROOT_TOKEN=secret uv run uvicorn curfew_api.app:create_app --factory --port 8000`. In another:
   - `curl localhost:8000/v1/health` → `{"status":"ok","db":"ok"}` 200.
   - `curl localhost:8000/v1/openapi.json` → 200.
   - `curl localhost:8000/v1/some-protected-stub` → 401.
   - `curl -H "Authorization: Bearer secret" localhost:8000/v1/some-protected-stub` → 200 (or 404 if no stub mounted; either confirms auth passed).
8. **Docker smoke** (if daemon available): `docker build -f api/Dockerfile . && docker run --rm -p 8000:8000 -e CURFEW_ROOT_TOKEN=secret <image>`. Curl `/v1/health` from the host.
9. Push → CI green on PR #9.

## Branch + PR

- Branch: `runtime-walking-skeleton` off `main`.
- Single commit covering config + db + app + auth + health + tests + Dockerfile + ADR if needed (probably not — this is a straight implementation of PLAN.md §"Authentication" and §"Configuration and settings", no new architectural decisions).
- Open PR #9.

## Critical files for the implementer to reference

- `docs/PLAN.md` §"Configuration and settings" lines 281–325 (BaseSettings spec).
- `docs/PLAN.md` §"Authentication" lines 329–333 (root bearer V1, slot for per-user later).
- `docs/PLAN.md` §"OpenAPI contract" lines 339–341 (Pydantic v2 + auto-emitted OpenAPI).
- `docs/PLAN.md` API surface lines 380–400 (the `/v1/health` endpoint shape).
- `docs/DECISIONS.md` ADR-011 (config vs settings split — informs config.py shape).
- `core/curfew/models.py` (existing — the `Settings` table is the *runtime* settings, distinct from the boot-time `Settings` BaseSettings class. Different concept; same English word. Document this in `core/curfew/config.py` to avoid confusion).
