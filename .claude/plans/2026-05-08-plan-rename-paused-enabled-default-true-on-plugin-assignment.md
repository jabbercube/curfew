# Plan — Rename `paused` → `enabled` (default `true`) on plugin assignments

## Context

The `plugins.paused` flag (DB column + SQLModel field + schemas + runtime + UI types) is a negative boolean — operators set `paused=true` to skip a plugin. Reading code that says `if not live.paused` adds an inversion every time. Switching to `enabled` (default `true`) reads naturally and matches how operators talk about the toggle (\"is this plugin on?\"). The flip is mechanical but touches the DB schema, every layer that mirrors it, the OpenAPI types regenerated for the SPA, the user-facing docs, and a couple of past plan files.

Inventory (from Phase 1 explore): ~50 references across `core/`, `api/`, tests, `web/src/api/schema.ts`, `docs/`, and two plan files. CLI is empty for plugins, so nothing to change there. The column was created in `0001_initial.py`; per the user's choice we land a new revision rather than editing the initial.

## Approach

### Schema migration — new revision `0003_plugin_paused_to_enabled.py`

`upgrade()`:

```python
with op.batch_alter_table("plugins") as batch:
    batch.drop_column("paused")
    batch.add_column(
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true())
    )
# Drop the server_default so future inserts go through the model default.
with op.batch_alter_table("plugins") as batch:
    batch.alter_column("enabled", server_default=None)
```

`downgrade()` is the inverse — drop `enabled`, re-add `paused` with `server_default=sa.false()` and clear it. This is a destructive drop+recreate (any previously-paused row becomes enabled on upgrade and vice versa); per Phase 1 there are no real assignments in production state.

### SQLModel + dataclass

- `core/curfew/models.py` — `PluginAssignment.paused: bool = Field(default=False)` → `enabled: bool = Field(default=True)`.
- `core/curfew/plugin_runtime.py` — `AssignedInstance.paused: bool` → `enabled: bool`.

### Runtime logic inversion

`core/curfew/plugin_runtime.py`:
- `register(..., paused: bool = False)` → `register(..., enabled: bool = True)`.
- `update(..., paused: bool | None = None)` → `update(..., enabled: bool | None = None)`. Inside, `if paused is not None: replace(live, paused=paused)` → `if enabled is not None: replace(live, enabled=enabled)`.
- Dispatch filter: `if not live.paused and self._governs(live, username)` → `if live.enabled and self._governs(live, username)`.

### Schemas

`core/curfew/schemas.py`:
- `PluginAssignmentRead.paused: bool` → `enabled: bool`.
- `PluginAssignmentUpdate.paused: bool | None = None` → `enabled: bool | None = None`.
- `PluginSnapshotRow.paused: bool` → `enabled: bool`.
- Update the docstring on `PluginAssignmentRead` (\"the in-memory paused flag\" → \"the in-memory enabled flag\").

### API routes

`api/curfew_api/routes/plugins.py`:
- `_row_to_read` — `paused=row.paused` → `enabled=row.enabled`.
- `create_assignment` — drop the explicit `paused=False` arg (model default handles it) or set `enabled=True`.
- `create_assignment` runtime call — `paused=row.paused` → `enabled=row.enabled`.
- `update_assignment` — substitute every `paused` to `enabled` (runtime call arg, DB row write, docstring \"`users` and `paused`\").

`api/curfew_api/app.py`:
- Hydrate registration — `paused=row.paused` → `enabled=row.enabled`.

### Frontend types

`web/src/api/schema.ts` is generated. Regenerate via `just web-schema` (needs `just serve` running with the new code). Then `just web-check` to ensure nothing in `web/src/` references the old field name. Inventory shows one hit — the generated interface field — so no hand-written TS code should break.

### Docs

- `docs/PLAN.md` — update §Tables row for `plugins`, §Plugin lifecycle (\"flips a `paused` flag\"), and the API surface lines for `GET /v1/plugins` and `PATCH /v1/plugins/...`.
- `docs/PLUGINS.md` — example insertion (`paused=false` → `enabled=true`) and the lifecycle paragraph (\"`paused=true`. Reconciliation skips this plugin\" → \"`enabled=false`. Reconciliation skips this plugin\").
- `docs/DECISIONS.md` ADR-007 — column list and the \"assigned-but-paused\" sentence.

### Plan files (per user request)

- `.claude/plans/2026-05-05-step-2-storage-kernel-sqlmodel-models-initial-migration.md` — line 131.
- `.claude/plans/2026-05-08-step-11-plugin-runtime-and-reconcile.md` — every `paused` reference (≈10 lines).

### Tests — rename + flip values

- `core/tests/test_plugin_runtime.py`:
  - `test_update_paused_doesnt_reinstantiate` → `test_update_enabled_doesnt_reinstantiate`. Body: `paused=True` → `enabled=False`; assertion `live.paused is True` → `live.enabled is False`.
  - `test_dispatch_skips_paused` → `test_dispatch_skips_disabled`. Body: `paused=True` → `enabled=False`.
- `api/tests/test_plugins_assignments.py`:
  - `test_patch_paused` → `test_patch_enabled`. PATCH body `{"paused": True}` → `{"enabled": False}`. Assertion `r.json()["paused"] is True` → `r.json()["enabled"] is False`. Default-state assertion `item["paused"] is False` → `item["enabled"] is True`.
- `api/tests/test_plugins_reconcile.py`:
  - `_setup_kid_and_plugin` helper: kwarg `paused: bool = False` → `enabled: bool = True`. The PATCH call inside flips the value sent.
  - `test_paused_plugin_doesnt_reconcile` → `test_disabled_plugin_doesnt_reconcile`. Helper invocations `paused=True` → `enabled=False`. Comments \"Lock while paused\" → \"Lock while disabled\".
  - `test_safety_net_picks_up_missed_lock` — the unpause PATCH (`{"paused": False}`) becomes `{"enabled": True}`.

## Files to touch

| File | Change |
|---|---|
| `api/migrations/versions/0003_plugin_paused_to_enabled.py` | New. Drop `paused`, add `enabled` (default true). |
| `core/curfew/models.py` | Rename + flip default. |
| `core/curfew/plugin_runtime.py` | Rename `AssignedInstance` field, flip method args/defaults, invert dispatch filter. |
| `core/curfew/schemas.py` | Rename in `PluginAssignmentRead`/`Update`/`PluginSnapshotRow` + docstring. |
| `api/curfew_api/routes/plugins.py` | Rename across `_row_to_read`, `create_assignment`, `update_assignment` + docstring. |
| `api/curfew_api/app.py` | Rename hydrate kwarg. |
| `core/tests/test_plugin_runtime.py` | Rename two tests + flip values. |
| `api/tests/test_plugins_assignments.py` | Rename test + flip JSON bodies + flip default-state assertion. |
| `api/tests/test_plugins_reconcile.py` | Rename helper kwarg + tests + flip values + comments. |
| `web/src/api/schema.ts` | Regenerated (don't hand-edit). |
| `docs/PLAN.md` | §Tables, §Plugin lifecycle, API surface lines. |
| `docs/PLUGINS.md` | Example + lifecycle paragraph. |
| `docs/DECISIONS.md` | ADR-007 column list + sentence. |
| `.claude/plans/2026-05-05-step-2-...md` | Line 131. |
| `.claude/plans/2026-05-08-step-11-...md` | All `paused` references. |

## Reused utilities (no new infrastructure)

- Alembic batch mode (already used by `0001_initial.py` + `0002_per_user_auth.py`).
- `just web-schema` recipe regenerates `web/src/api/schema.ts` from the live `/v1/openapi.json`.
- `dataclasses.replace` already used in `runtime.update`.

## Verification

1. `just check` — ruff, ruff format, mypy, pytest (all 250 tests, with renames). Web check (`bun run typecheck` + lint + tests + build) clean after `just web-schema`.
2. Migration round-trip:
   - `cd api && uv run alembic upgrade head` against a fresh `/tmp/v3-state.sqlite`; inspect column → `enabled BOOLEAN NOT NULL`.
   - `uv run alembic downgrade -1` → column reverts to `paused`.
   - `uv run alembic upgrade head` again → back to `enabled`. Idempotent.
3. End-to-end sanity (re-run the step-11 live smoke shape):
   - `just serve`. `POST /v1/plugins` body `{"type":"reftest_plugin","config":{"sentinel_path":"/tmp/v3-sent"},"users":["kid1"]}` → response includes `"enabled": true`.
   - `POST /v1/users/kid1/lock` → sentinel appears.
   - `PATCH /v1/plugins/reftest_plugin/default` body `{"enabled": false}` → response shows `"enabled": false`.
   - `POST /v1/users/kid1/lock` again → sentinel does *not* update (disabled plugin skipped). Audit shows no new `plugin.reconcile_failed` row.
   - `PATCH ... '{"enabled": true}'` → re-enable. Lock toggle now updates the sentinel again.
   - Clean up `/tmp/v3-*` files + kill server.
4. SPA: `just web-dev` + assigned plugin in the future plugin admin UI (n/a today — only relevant when that page lands; the schema-type regeneration is what blocks future work).

## Branch + PR

- Branch: `plugin-enabled-flag` off `main`.
- One commit, one PR. Estimated diff: ~80 lines code change + ~30 doc/plan-file edits + 1 new migration file (~25 lines).
