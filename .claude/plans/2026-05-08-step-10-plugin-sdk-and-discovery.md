# Step 10 — Plugin SDK + discovery

## Context

The plugin surface is one of the two big remaining kernel commitments (the other is the agent surface). PLAN.md §"Plugin lifecycle" + ADR-005 + ADR-013 + PLUGINS.md describe the contract: drop a folder into a `CURFEW_PLUGINS_DIRS` entry, curfew imports it at startup, the operator assigns it, the core calls its `reconcile()` when state changes.

This slice lands the **SDK + discovery half** of that contract:

- A `Plugin` base class plugin authors subclass, with the `Scope` and `ReconcileResult` types they consume/return.
- A loader that walks `CURFEW_PLUGINS_DIRS`, validates each plugin's `manifest.toml`, imports `plugin.py`, finds the leaf `Plugin` subclass, and builds an in-memory registry — surviving (and surfacing) per-plugin errors.
- `GET /v1/plugins/types` so the operator can see what's discovered and any per-plugin discovery errors.

The runtime half — `plugins` table CRUD, in-memory instance manager, reconcile dispatch on lock-toggle, safety-net resync, and the reftest plugin's sentinel logic — lands in step 11. After step 11, the kernel acceptance plugin path (PLAN.md §"Acceptance: kernel done" steps 11–16) passes end-to-end.

## Scope

**In:**

- `core/curfew/plugin.py` — `Plugin` base class (one method: `async def reconcile(scope) -> ReconcileResult`), `Scope` dataclass (`user, locked, reasons`), `ReconcileResult` (with `ok()` and `error()` helpers), `PluginManifest` dataclass.
- `core/curfew/plugin_loader.py` — `discover_plugins(dirs)` returns a `PluginRegistry` of `{type: PluginType}`. Each `PluginType` carries the manifest, the resolved entry-point class, and (for failed plugins) the discovery error. Walks colon-separated `CURFEW_PLUGINS_DIRS` in order; later-wins on duplicate `type`. Catches errors per-plugin so one bad plugin doesn't break startup.
- `core/curfew/__init__.py` — re-export `Plugin`, `Scope`, `ReconcileResult`, `PluginManifest`. Drop the old `Plugin` (DB) re-export; replace with `PluginAssignment`.
- `core/curfew/models.py` — rename DB class `Plugin` → `PluginAssignment` to free up `Plugin` for the SDK class. Table name stays `"plugins"`; no migration.
- `core/curfew/schemas.py` — update `PluginSnapshotRow` import target; rename of the model is internal so no shape change.
- `core/tests/test_models.py` — follow the `Plugin → PluginAssignment` rename.
- `core/tests/test_plugin_sdk.py` — new. `Scope` and `ReconcileResult` constructors, default `reconcile` raises `NotImplementedError`.
- `core/tests/test_plugin_loader.py` — new. The gnarly discovery edge cases: missing `manifest.toml`, no `Plugin` subclass (skip + error), multiple subclasses → leaf wins, malformed `plugin.py` (skip + error), override on duplicate `type` (later wins), multiple dirs scanned in order, `requirements.txt` present without auto-install (warning recorded, plugin still loads if its imports don't fail).
- `api/curfew_api/routes/plugins.py` — new. `GET /v1/plugins/types` only this PR. Returns `[{type, name, version, description, config_schema, error?}]`.
- `api/curfew_api/app.py` — call `discover_plugins(config.plugins_dirs)` once at startup, stash the registry on `app.state.plugin_registry`. Mount the new router. The status route (`/v1/plugins/types`) reads from `app.state`.
- `api/curfew_api/routes/system.py` — follow the `PluginAssignment` rename.
- `api/tests/test_plugins_types.py` — new. 401 unauth, 200 lists discovered types, errors surfaced for malformed plugin folders.
- `plugins/reftest-plugin/manifest.toml` — new. `type = "reftest_plugin"`, `config_schema = "Config"`.
- `plugins/reftest-plugin/plugin.py` — new. Empty `Config`, no-op `reconcile` returning `ReconcileResult.ok()`. Step 11 turns this into the sentinel-writing version.

**Out (next slice — step 11):**

- `plugins` table CRUD endpoints (`POST/PATCH/DELETE/GET /v1/plugins`).
- In-memory `PluginRuntime` (instance manager): instantiate on assign, drop on unassign, re-instantiate on PATCH.
- Reconcile dispatch: hooked into `_set_manual_lock` via `BackgroundTasks`.
- Safety-net resync: lifespan task on `plugin_resync_seconds`.
- Per-call timeout (`plugin_reconcile_timeout_seconds`) + audit-on-failure.
- `reftest-plugin` sentinel logic + e2e test.

## Notable design choices

- **Rename `models.Plugin` → `models.PluginAssignment`** to clear the namespace. Plugin authors import `Plugin` from `curfew.plugin` (per PLUGINS.md). The DB row is the operator's *assignment* of a plugin type to a set of users with a config — `PluginAssignment` says what it is and matches the CLI verbs (`assign`/`unassign`). No migration: `__tablename__` is set explicitly.
- **`Scope` is `{user, locked, reasons}`** — no `target_apps` yet. PLAN.md's "per-rule user config" decision is deferred until the first non-kernel rule needs it; same applies to the apps an agent/plugin should target. PLUGINS.md examples reference `scope.target_apps` aspirationally; we'll add it (non-breaking — `Scope` is a Pydantic-style dataclass) when the first plugin needs it. The reftest plugin doesn't.
- **No `requirements.txt` auto-install.** PLAN.md ADR-013 lists it as optional; running pip-install at every startup is slow and a failure mode (network, version conflicts) for no immediate gain. Reftest has no deps; real plugins (adguard) get this when they land or as part of the curfew-core image build. Loader logs a single line if `requirements.txt` is present, recorded on the registry entry — operator visible via `/v1/plugins/types`.
- **Errors don't break startup.** Per ADR-013 + PLUGINS.md §"What can go wrong": a malformed plugin is logged, recorded on the registry entry, surfaced via `/v1/plugins/types`, and skipped. Other plugins still load; curfew-core still starts. This is non-negotiable for a homelab; one operator-authored plugin shouldn't take down the whole core.
- **Leaf-class entry point.** A plugin with `class BaseAdGuardClient(Plugin)` + `class AdGuardPlugin(BaseAdGuardClient)` registers `AdGuardPlugin` (the leaf — no `Plugin` subclass extends it within the module). Zero subclasses → discovery error. Multiple leaves → discovery error (ambiguous). Implementation: walk subclasses of `Plugin` defined in the imported module's namespace; the leaf set is `{cls for cls in subclasses if no other subclass extends cls}`.
- **Discovery runs once at startup.** The registry lives on `app.state.plugin_registry`. Adding a new plugin folder requires a curfew-core restart — explicit in PLAN.md and PLUGINS.md. Toggling existing plugins (pause / unpause) doesn't (step 11).
- **`/v1/plugins/types` auth = `Operator` (any authenticated).** Discovery results aren't sensitive. Tightening to admin-only would just nag managers in the future GUI for no security gain.
- **Module loading via `importlib.util.spec_from_file_location`.** Plugin folders aren't on `sys.path`; we load each `plugin.py` by absolute file path with a synthetic module name (`curfew_plugin_<type>`). Stays out of `sys.path` to avoid leaking plugin-internal modules into the global namespace.

## Files to touch

| File | Change |
|---|---|
| `core/curfew/plugin.py` | New. `Plugin` SDK base class, `Scope`, `ReconcileResult`, `PluginManifest`. |
| `core/curfew/plugin_loader.py` | New. `discover_plugins(dirs)` + `PluginRegistry` + `PluginType`. |
| `core/curfew/models.py` | Rename `Plugin` class → `PluginAssignment`. Tablename unchanged. |
| `core/curfew/__init__.py` | Drop `Plugin` (DB) export; add `PluginAssignment`, `Plugin` (SDK), `Scope`, `ReconcileResult`, `PluginManifest`. |
| `core/curfew/schemas.py` | No shape change; `PluginSnapshotRow` already imports nothing model-named, so just verify. |
| `core/tests/test_models.py` | Rename `Plugin` → `PluginAssignment` in imports + setup. |
| `core/tests/test_plugin_sdk.py` | New. `Scope`, `ReconcileResult.ok/.error`, default reconcile raises. |
| `core/tests/test_plugin_loader.py` | New. Discovery edge cases. |
| `api/curfew_api/routes/plugins.py` | New. `GET /v1/plugins/types`. |
| `api/curfew_api/routes/system.py` | Rename `Plugin` → `PluginAssignment` in import + query. |
| `api/curfew_api/app.py` | Call `discover_plugins` at startup; stash registry on `app.state`; mount `plugins` router. |
| `api/tests/test_plugins_types.py` | New. 401, 200 list, errors surfaced. |
| `plugins/reftest-plugin/manifest.toml` | New. |
| `plugins/reftest-plugin/plugin.py` | New. No-op reconcile (step 11 wires the sentinel). |
| `api/tests/test_migration.py` | Drive-by: `monkeypatch.delenv("CURFEW_DB_PATH")` in fixtures so `api/migrations/env.py`'s env-var override doesn't redirect alembic to the justfile's pinned DB. Pre-existing — `just test` was failing 12 migration tests on every PR since the justfile pinned `CURFEW_DB_PATH`. Fixed here so this PR's own `just check` is clean. |
| `core/tests/test_config.py` | Drive-by: same `delenv` in `test_defaults_apply_when_only_token_set` (it asserts the default `db_path = "state.sqlite"`, which the leaked env var clobbered). |

## Reused utilities (no new infrastructure)

- `core/curfew/config.py:Settings.plugins_dirs` — already a colon-separated string in config; loader splits on `:`.
- `api/curfew_api/auth.py:Operator` — auth dep for the types endpoint.
- `api/tests/conftest.py` — `client`, `auth`, `configured_db` fixtures.

## Verification

- `just check` clean (`ruff` + `ruff format --check` + `mypy` + `pytest`).
- New test count: ~10 in `test_plugin_loader.py`, 3-4 in `test_plugin_sdk.py`, 3 in `test_plugins_types.py`. Existing model tests follow the rename.
- Live smoke: `just serve`, then `curl -H 'Authorization: Bearer ...' :8000/v1/plugins/types` returns `[{type: "reftest_plugin", ...}]`. Drop a deliberately-broken folder into `plugins/`, restart, see it surfaced with `error: "..."`.

## Branch + PR

- Branch: `plugin-sdk-discovery` off `main`.
- One commit, one PR. Estimated diff: ~600 lines across ~12 files (most lines in tests).
