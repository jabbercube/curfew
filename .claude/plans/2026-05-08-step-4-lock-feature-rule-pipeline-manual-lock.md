# Step 4 — Lock feature: rule pipeline + manual_lock + audit + endpoints

## Context

Step 3 (PR #9) landed the runtime walking skeleton — auth, `/v1/health`, an empty FastAPI app. Step 4 makes the system *do something*. After this PR, an operator can `POST /v1/users` → `POST /v1/users/{user}/lock` → `GET /v1/users/{user}/status` (returns `{locked: true, reasons: [{kind: "manual_lock"}]}`) → `POST /v1/users/{user}/unlock`. Every write is audited.

This is the first vertical kernel slice. The rule-pipeline architecture introduced here is what every future lock condition (schedule, budget, shared-device) plugs into — they register a callable into the right scope; no API surface changes. PLAN.md §"Lock status rule pipeline" + ADR-008 are the load-bearing references.

## Scope

**In:**
- `core/curfew/schemas.py` — `Reason` (kind + extra=allow per PLAN.md), `LockStatus`, `UserCreate`, `UserRead`. Lives in core so the CLI and SDK consumers can import the same types.
- `core/curfew/rules.py` — `RulePipeline`, `user_scope` and `device_scope` module-level singletons, `manual_lock_rule` (returns `Reason(kind="manual_lock")` when `user_locks.manual_lock` is set; skips users with `managed=False` per PLAN.md §"Users"), `register_kernel_rules()` called by `create_app()` at startup.
- `core/curfew/audit.py` — `record_audit(session, *, actor, action, target_kind, target_id, payload?)` helper. Each write route calls it; the helper flushes but doesn't commit so audit + write commit atomically together.
- `api/curfew_api/routes/users.py` — `POST /v1/users` (201/409), `GET /v1/users/{user}` (200/404). Minimum CRUD to enable the lock flow; rest in step 5.
- `api/curfew_api/routes/locks.py` — `POST /v1/users/{user}/lock`, `/unlock`. Both upsert `user_locks`, audit, and return live `LockStatus`.
- `api/curfew_api/routes/status.py` — `GET /v1/users/{user}/status`, `GET /v1/devices/{device}/status`. Runs the relevant rule scope.
- `api/curfew_api/app.py` — registers kernel rules at startup; mounts new routers.
- `api/Dockerfile` — bakes `ENV CURFEW_DB_PATH=/app/state.sqlite` so bare `docker run -e CURFEW_ROOT_TOKEN=…` works without remembering to also set the DB path. (Fix caught while smoke-testing this PR end-to-end via docker.)

**Out (next slice):**
- Full user CRUD (list/patch/delete) and full device + app CRUD.

## Notable design choices

- **Audit as a helper, not ASGI middleware.** PLAN.md describes "a single audit middleware"; the implementation is a single `record_audit()` function each write route calls. Same outcome ("every write is audited the same way") with no path-parsing magic to derive `target_kind`/`target_id` — each route knows its own target.
- **Rules are callables, not classes.** `Rule = Callable[[Session, str], Reason | None]`. Promotes to a class when richer rules need config or state.
- **`Reason` uses `extra="allow"`.** PLAN.md: "Adding detail to a reason kind is a non-breaking change. Clients render `kind` and ignore unknown fields." A tagged union per kind would be more type-safe but every new rule = new union member; the flexible model matches PLAN.md's intent.
- **`managed=False` skip lives in the rule (not the endpoint).** PLAN.md says lock rules don't apply to unmanaged users. Encoding it in `manual_lock_rule` (and the status endpoint as belt-and-suspenders) means future rules inherit the same skip without per-rule work.

## Verification

- `uv sync --all-packages --group dev`, ruff, ruff format, mypy, pre-commit all clean.
- **`uv run pytest` — 70 passed** (+24 from PR #10's 46): 9 new in `test_rules.py`, 15 new in `test_locks.py`.
- TestClient lock flow end-to-end: create → lock → status (locked + manual_lock reason) → unlock → status (unlocked).
- Live docker container also exercises the full flow over HTTP after `just docker-build && docker run -e CURFEW_ROOT_TOKEN=secret -p 9876:8000 curfew-core:dev`.

## Branch + PR

- Branch: `lock-feature` off `main`.
- One commit `066d4c9`. 11 files changed, 764 insertions.
- PR #11, merged.
