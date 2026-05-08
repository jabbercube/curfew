# Step 6 — Settings + system snapshot

## Context

After PR #13 every kernel data table (users, devices, apps) has full CRUD — the operator can manage entities through the API. What they still can't do over HTTP: read or change runtime tunables (the `settings` singleton row), or take a one-shot diagnostic dump of system state.

PLAN.md "API surface (kernel)" lists both as kernel-tier endpoints:

```
GET    /v1/system/snapshot    full system snapshot (debug; not the primary read path)
GET    /v1/settings           runtime-mutable kernel settings
PATCH  /v1/settings           update one or more
```

(PLAN.md uses `/v1/admin/snapshot`; we're renaming the namespace to `/v1/system/` to avoid collision with `User.role=admin`. PLAN.md edit is part of this PR.)

This slice closes that gap. After this PR the kernel API surface is complete enough to start on agents/plugins (the next feature surface) without further base-layer churn.

## Scope

**In:**

- `core/curfew/schemas.py` — `SettingsRead`, `SettingsUpdate` (all-optional fields, positive-int validation), `SystemSnapshot` aggregate response.
- `api/curfew_api/routes/settings.py` — new file. `GET /v1/settings` (returns the singleton row), `PATCH /v1/settings` (partial update, audited).
- `api/curfew_api/routes/system.py` — new file. `GET /v1/system/snapshot` (read-only aggregate dump).
- `api/curfew_api/app.py` — mount the two new routers.
- `core/curfew/__init__.py` — re-export new schemas.
- `docs/PLAN.md` — update the API surface line from `/v1/admin/snapshot` to `/v1/system/snapshot`; note the rename in passing.
- `api/tests/test_settings.py` — new file: GET returns defaults, PATCH partial, PATCH validates positive ints, PATCH audits with changed fields, unauthenticated → 401.
- `api/tests/test_system_snapshot.py` — new file: empty system, populated system (users + devices + apps + lock), audit_log/agent_tokens excluded, unauthenticated → 401.

**Out (explicitly):**

- Per-user role-based auth. PLAN.md ("Authentication") says V1 is operator-only via root token; the snapshot endpoint doesn't need a separate auth tier.
- Settings *deletion* / reset-to-defaults. PATCH suffices; resetting is an operator concern best handled by re-applying the defaults manually.
- Streaming / paginated snapshot. V1 fits comfortably in one JSON response — file the back-pressure concern when it actually bites.

## Notable design choices

- **Snapshot excludes `audit_log` and `agent_tokens`.** Audit log is unbounded and grows linearly; agent_tokens stores secret hashes that have no business in a diagnostic dump. The snapshot reports a `counts` block (e.g. `{"audit_log": 1234, "agent_tokens": 3}`) so operators still see they exist without dumping rows.
- **Snapshot reuses existing Read schemas where they exist** (`UserRead`, `DeviceRead`, `AppRead`, `SettingsRead`). For tables without a current Read schema (agents, plugins, user_locks, manifests), define minimal `*SnapshotRow` Pydantic models inline in `schemas.py` rather than premature full Read schemas — they'll get proper Read schemas when the corresponding CRUD lands.
- **Snapshot is unaudited.** It's a read. Audit volume from clicking "snapshot" repeatedly would drown signal.
- **Settings PATCH audits the changed fields only**, mirroring the `user.update` pattern (`payload=update_data` from `model_dump(exclude_unset=True)`). `target_id="singleton"` since the row is keyed `id=1` and that's the conventional name for it.
- **Positive-int validation at the schema layer.** `agent_tick_seconds=0` would silently busy-loop the agent; `audit_retention_days=-1` would be nonsense. `Field(gt=0)` on each tunable in `SettingsUpdate` rejects bad values at the boundary with a 422.
- **No "ETag / If-Match" on settings PATCH.** Concurrent operator edits aren't a real concern for a homelab with one operator. Add when a real conflict surfaces.

## Files to touch

| File | Change |
|---|---|
| `core/curfew/schemas.py` | Add `SettingsRead`, `SettingsUpdate`, `SystemSnapshot` + minimal snapshot row schemas for agents/plugins/user_locks/manifests. |
| `core/curfew/__init__.py` | Re-export the new schemas. |
| `api/curfew_api/routes/settings.py` | New: GET + PATCH on `/v1/settings`. Reuses `record_audit`, `Operator` dep, `get_session` dep. |
| `api/curfew_api/routes/system.py` | New: GET on `/v1/system/snapshot`. Reuses `Operator` dep, `get_session` dep. |
| `api/curfew_api/app.py` | Two `include_router` lines (after `status.router`). |
| `docs/PLAN.md` | Rename `/v1/admin/snapshot` → `/v1/system/snapshot` in the API surface table. |
| `api/tests/test_settings.py` | New. Uses `client` / `auth` / `configured_db` fixtures from `conftest.py`. |
| `api/tests/test_system_snapshot.py` | New. Same fixtures. |

## Reused utilities (no new infrastructure)

- `core/curfew/audit.py:record_audit` — for settings PATCH; flushes-not-commits semantics already established.
- `core/curfew/db.py:get_session` — FastAPI session dep.
- `api/curfew_api/auth.py:Operator` — bearer auth.
- `core/curfew/models.py:AuditTargetKind.SETTINGS` — already exists, no enum change needed.
- `api/tests/conftest.py` — `client` / `auth` / `configured_db` fixtures.

## Verification

- `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pre-commit run --all-files` — clean.
- `uv run pytest` — 119 → ~135 (16ish new). All green.
- Live Docker:
  - `just docker-build && docker run -e CURFEW_ROOT_TOKEN=secret -p 9876:8000 curfew-core:dev` then:
  - `curl -H 'Authorization: Bearer secret' localhost:9876/v1/settings` returns the seeded defaults.
  - `curl -X PATCH -H 'Authorization: Bearer secret' -H 'Content-Type: application/json' -d '{"agent_tick_seconds": 30}' localhost:9876/v1/settings` returns the row with the new value.
  - `curl -X PATCH ... -d '{"agent_tick_seconds": 0}' ...` → 422.
  - `curl -H 'Authorization: Bearer secret' localhost:9876/v1/system/snapshot` returns `{users: [], devices: [], apps: [], agents: [], plugins: [], user_locks: [], manifests: [], settings: {...}, counts: {audit_log: N, agent_tokens: M}}` (post-fixtures).

## Branch + PR

- Branch: `settings-admin` off `main` (already created per task #52).
- One commit, one PR. Estimated diff: ~400 lines added across 7 files.
