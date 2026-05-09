# Step 11 — Plugin runtime + reconcile + reftest sentinel

## Context

Step 10 (PR #23) shipped the SDK + discovery half of the plugin surface — `Plugin` base class, manifest parsing, leaf-class loader, `GET /v1/plugins/types`. Operators can see what plugin types are available but can't yet assign one or get a reconcile to fire.

This slice closes the loop. After this PR:

- The operator assigns a discovered plugin via `POST /v1/plugins` and curfew-core instantiates it.
- A user lock toggle schedules a background reconcile on every plugin governing that user.
- A safety-net resync runs every `plugin_resync_seconds` and re-calls reconcile on every governing plugin for every governed user (catches missed events through restarts).
- The reftest plugin writes a sentinel file when locked / removes it when unlocked, so the **kernel acceptance plugin path** (PLAN.md §"Acceptance: kernel done" steps 11–16) passes end-to-end.

PLAN.md §"Plugin lifecycle" + ADR-005 + PLUGINS.md §"Lifecycle" are the load-bearing references.

## Scope

**In:**

- `core/curfew/plugin_runtime.py` — new. The in-memory instance manager. Holds `{(type, instance_id): _Live}` where `_Live` carries the instantiated `Plugin`, the resolved governed-users list, and the paused flag. Mutations (`assign`, `unassign`, `update`, `set_paused`) are guarded by an `asyncio.Lock`. `dispatch_for_user(username)` finds every governing instance and schedules `_run_one_reconcile` for each. `_run_one_reconcile` wraps the plugin's `reconcile()` in `asyncio.wait_for(..., timeout=plugin_reconcile_timeout_seconds)` and audits failures (timeout, exception, error result). `safety_net_resync()` walks every governed user and dispatches.
- `core/curfew/schemas.py` — `PluginAssignmentRead`, `PluginAssignmentCreate` (`type`, optional `instance_id` defaulting to `"default"`, `config: dict`, `users: list[str]`), `PluginAssignmentUpdate` (all-optional: `config`, `users`, `paused`).
- `core/curfew/__init__.py` — re-export `PluginRuntime`, the new schemas.
- `api/curfew_api/routes/plugins.py` — extend with the four CRUD verbs:
  - `GET /v1/plugins` — list assignments + paused/users (operator).
  - `POST /v1/plugins` — assign. Validates the discovered type exists + config matches its Pydantic schema; inserts the row; instantiates via the runtime. 422 on bad config, 404 on unknown type, 409 on duplicate `(type, instance_id)`. Admin only.
  - `PATCH /v1/plugins/{type}/{instance_id}` — partial update. If `config` changes, re-instantiate (PLAN.md option 1). If `users` changes, update the governed-users cache. If `paused` changes, flip the flag. Admin only.
  - `DELETE /v1/plugins/{type}/{instance_id}` — unassign. Drops from runtime. Admin only.
- `api/curfew_api/routes/locks.py` — after the lock/unlock commit, schedule `runtime.dispatch_for_user(username)` via FastAPI `BackgroundTasks`. The originating HTTP response returns immediately; reconciles run after.
- `api/curfew_api/app.py` — switch to FastAPI lifespan context manager. At startup: build the runtime, load every existing `plugin_assignments` row into memory, start an `asyncio` task that loops every `plugin_resync_seconds` (read fresh on each iteration so settings PATCHes take effect) and calls `runtime.safety_net_resync()`. At shutdown: cancel the task cleanly.
- `plugins/reftest-plugin/plugin.py` — replace the no-op reconcile with sentinel logic. `Config` gains `sentinel_path: str`. Locked → write the user's name to that path; unlocked → remove the file (idempotent if already absent). Used by the e2e test to assert reconcile actually ran.
- `core/tests/test_plugin_runtime.py` — new. Assign/unassign mutate the in-memory map. PATCH on `config` re-instantiates (assert `__init__` ran twice). PATCH on `users` rewires governance. PATCH on `paused` skips dispatch. `dispatch_for_user` picks instances whose `users` list contains the username or `["*"]`. Paused instances skipped. Timeout cancels the call and audits a `plugin.reconcile_failed`. An exception in `reconcile` is caught + audited but doesn't propagate. `safety_net_resync` calls reconcile for every governed user × every governing plugin.
- `api/tests/test_plugins_assignments.py` — new. CRUD endpoints: list (empty + populated), create (happy + 422 bad config + 404 unknown type + 409 duplicate), patch (single field, multi-field, paused toggle), delete (200 + 404). Audit rows. Auth: 401 unauth, 403 manager.
- `api/tests/test_plugins_reconcile.py` — new. e2e: configure `CURFEW_PLUGINS_DIRS` at the repo's `plugins/`; assign reftest_plugin with `sentinel_path=<tmp>/sentinel`; lock kid1 → wait for reconcile → assert sentinel exists with `kid1` content; unlock → assert removed. Same flow but kill the lock-triggered reconcile (paused at the moment of lock); safety-net resync picks it up on its next tick. Failed reconcile (point sentinel_path at an unwritable dir) → next lock toggle still returns 200; audit shows `plugin.reconcile_failed`.

**Out (next slice — beyond the kernel):**

- `requirements.txt` auto-install (PLAN.md ADR-013 lists this as optional / future).
- Plugin SDK helper for HTTP retry/timeout (PLAN.md §"Plugin SDK"). The first plugin (`adguard`) drives that.
- `target_apps` on `Scope` (PLAN.md per-rule user config — deferred until first non-kernel rule lands).
- Concrete plugins (adguard, smart-plug, router-acl). Each is a feature on top of the kernel.

## Notable design choices

- **Re-instantiate on config PATCH (PLAN.md option 1).** Cleanest semantics for the operator: PATCH is "give me this plugin running with this config." Plugin authors can keep `__init__` cheap (or expensive — their call) and don't need to remember to re-read config inside `reconcile`. `users` and `paused` changes don't re-instantiate.
- **`asyncio.Lock` for runtime mutations.** Two operators submitting concurrent assigns on the same `(type, instance_id)` lose to the DB unique constraint anyway, but the lock keeps the in-memory map consistent during the brief window between insert + instantiate. Cheap at homelab scale.
- **Reconcile dispatch via `BackgroundTasks`, not bare `asyncio.create_task`.** FastAPI runs background tasks after the response is sent, with built-in error containment. `create_task` would work but loses the framework hook (and tests would have to await the task explicitly).
- **Per-call timeout via `asyncio.wait_for`.** Cancels a hung plugin's coroutine and treats it as a failure. Plugins can still hang the entire event loop if they do blocking IO without `await` — that's a class of bug the plugin author has to avoid (called out in PLUGINS.md).
- **Reconcile failures are audited, never raised.** PLAN.md ADR-003 + PLUGINS.md §"What can go wrong": originating request must already have returned successfully. Audit kind is `plugin.reconcile_failed`; payload carries `{type, instance_id, user, error}`.
- **Safety-net loop reads `plugin_resync_seconds` fresh each iteration.** A settings PATCH that lowers the resync interval takes effect on the next tick, no restart needed. Same pattern that the agent SDK will use for `agent_tick_seconds` in the agent-surface PR.
- **Loading at startup.** All existing `plugin_assignments` rows get instantiated when the app starts. A row whose plugin type is no longer discoverable (operator removed the folder) is logged and skipped — the row stays in the DB so the operator can decide whether to delete it or restore the folder. This is the "graceful degradation" path: a missing plugin folder doesn't break startup.
- **Username vs user_id in `users` JSON column.** The column already stores usernames (per the existing model + PLAN.md). When a user is renamed, existing assignments still target the old username until PATCHed — same trade-off as audit log keeping target_id strings. Acceptable for V1; if it becomes a real problem we add a normalisation pass.
- **`POST /v1/plugins` instance_id default.** Body field is optional; `None` resolves to `"default"` server-side. Matches the CLI shape (`curfew plugin assign adguard` for the single-instance case).

## Files to touch

| File | Change |
|---|---|
| `core/curfew/plugin_runtime.py` | New. `PluginRuntime` class. ~250 lines. |
| `core/curfew/schemas.py` | Add `PluginAssignmentRead`, `PluginAssignmentCreate`, `PluginAssignmentUpdate`. |
| `core/curfew/__init__.py` | Export the new schemas + `PluginRuntime`. |
| `api/curfew_api/routes/plugins.py` | Add the four CRUD handlers. |
| `api/curfew_api/routes/locks.py` | Inject `BackgroundTasks` + runtime; schedule dispatch after commit. |
| `api/curfew_api/app.py` | Lifespan context manager — load assignments + start resync task. |
| `plugins/reftest-plugin/plugin.py` | Sentinel logic. |
| `core/tests/test_plugin_runtime.py` | New. |
| `api/tests/test_plugins_assignments.py` | New. |
| `api/tests/test_plugins_reconcile.py` | New (e2e + safety-net + failure path). |
| `api/tests/conftest.py` | Possibly: `client_with_plugins` fixture if needed for the e2e tests. |

## Reused utilities (no new infrastructure)

- `core/curfew/plugin_loader.py:PluginRegistry` — runtime asks the registry for the type → resolved class + config model.
- `core/curfew/audit.py:record_audit` — for assignment writes + reconcile-failed audits.
- `core/curfew/models.py:PluginAssignment, Settings` — DB row + runtime tunables.
- `api/curfew_api/auth.py:Operator, RequireAdmin` — auth deps.

## Verification

- `just check` clean.
- New test count: ~12 in `test_plugin_runtime.py`, ~12 in `test_plugins_assignments.py`, ~5 in `test_plugins_reconcile.py`. Existing 207 tests still green.
- Live smoke (post-merge of step 10):
  - `just serve`, then `curl -X POST -H 'Authorization: Bearer ...' -H 'Content-Type: application/json' -d '{"type":"reftest_plugin","config":{"sentinel_path":"/tmp/sent"},"users":["*"]}' :8000/v1/plugins`.
  - `just user-create kid1 kid12345 member` then lock kid1; `/tmp/sent` appears with content `kid1`.
  - Unlock; `/tmp/sent` disappears.
  - Pause the plugin (`PATCH ... '{"paused": true}'`); lock again; sentinel does *not* appear.

## Branch + PR

- Branch: `plugin-runtime-reconcile` off `main` (post-#23 merge).
- One commit, one PR. Estimated diff: ~900 lines across ~10 files (most lines in tests).
