# AdGuard plugin (headless)

## Context

curfew already has the plugin runtime (discovery, instantiation, dispatch, safety-net resync, audit on failure) and the manual lock surface. What's missing is the first real plugin: AdGuard Home, which sinkholes a kid's DNS by setting per-MAC AdGuard client `upstreams` to `0.0.0.0`. PLAN.md calls this out as the first in-core plugin — it stress-tests discovery and the resync path.

The user has a working ~400-line standalone Python script (the seed of the project) that does the same thing against a YAML inventory. We're not porting that script; we're rebuilding the same behavior on top of curfew's plugin SDK and Devices table.

A GUI question came up — plugins eventually want their own pages (e.g. an AdGuard tab with a "synced devices" table and push/pull buttons). We're **not** building the plugin UI surface in this work. We're capturing the recommended direction (schema-driven, declarative plugin actions/panels rendered by the existing SPA) as ADR-017 so it's not re-litigated, then shipping AdGuard headless. Operators interact via API and CLI; lock state changes drive AdGuard automatically.

## Outcome

After this work:

1. The kernel ships ADR-017 documenting the future plugin-UI direction.
2. `Scope` carries the user's devices, so plugins can act on MAC granularity (one-time SDK extension; every future plugin benefits).
3. `plugins/adguard/` is a working, headless plugin: assign it once with AdGuard's URL + creds, lock a kid, kid's MACs get sinkholed; unlock, sinkhole clears.

## Approach

Two PRs, in order. Each is small and reviewable on its own.

### PR 1 — `Scope.devices` SDK extension

**Why first**: AdGuard fundamentally works at MAC granularity. Today `Scope` is `(user, locked, reasons)` — no path for `reconcile()` to learn the user's MACs without reaching into the curfew DB. The fix is a non-breaking field add (the SDK doc explicitly calls out adding fields to `Scope` as non-breaking). Smart-plug, router-acl, tailscale all need this same data; we're not building it just for AdGuard.

**Changes**:

- **`core/curfew/plugin.py`** — add a `DeviceRef` dataclass (`slug`, `type`, `os`, `mac: list[str]`, `managed`) and a `devices: list[DeviceRef] = field(default_factory=list)` field on `Scope`. Add to module docstring. Re-export `DeviceRef`.
- **`core/curfew/plugin_runtime.py`** — `dispatch_for_user`: after evaluating user_scope, query `Device` rows where `owner_id == user.id`, build `DeviceRef` list, pass into `Scope`. `safety_net_resync`: same. The query is small (one extra `select(Device)` per dispatched user); no batching needed at homelab scale.
- **Tests**:
  - Unit test in `core/tests/test_plugin_runtime.py` asserting `Scope.devices` is populated when the user has Devices, empty list when they don't.
  - Update `plugins/reftest-plugin/plugin.py` to optionally write the device-slug list alongside the username in the sentinel (or add a second sentinel for devices). Existing acceptance tests in `api/tests/test_plugins_reconcile.py` extend with one new assertion that `reconcile` sees the user's devices.
- **No model change.** `Device.mac` is already `list[str]`; we read it as-is. No IP field is needed (AdGuard accepts MAC-only client IDs).
- **No `PluginAssignment.users` change.** AdGuard will be assigned with `users=["*"]` typically, but the field stays — smart-plug-per-room scenarios still need it.

### PR 2 — `plugins/adguard/` plugin

**Files**:
- `plugins/adguard/manifest.toml`
- `plugins/adguard/plugin.py`
- `plugins/adguard/tests/test_plugin.py` (or wherever the test lives — see below)

**Config (Pydantic, in `plugin.py`)**:
```python
class Config(BaseModel):
    url: str                                # e.g. http://adguard.lan
    username: str
    password_env: str                       # env var holding AdGuard admin password (PLAN.md secret convention)
    sinkhole_upstream: str = "0.0.0.0"      # configurable; default matches the original script
```

**HTTP**: `httpx.AsyncClient` with Basic auth — already a curfew dep, so no `requirements.txt` quirk (which the loader currently can't auto-install per ADR-013's shared-env trade-off). Plugin reads `os.environ[config.password_env]` in `__init__` and constructs the client there.

**Reconcile behavior** (following the original script's mechanics):
1. Filter `scope.devices` to managed devices with non-empty `mac`. Skip+log others (no warning row in the audit log; this is normal for adult/shared devices).
2. `GET /control/clients`. Build a `{mac.lower(): client}` index.
3. **Block leg (`scope.locked = True`)**: for each remaining device:
   - If a client exists matching any of the device's MACs: if its `upstreams != [sinkhole_upstream]`, `POST /control/clients/update` with `data.upstreams = [sinkhole_upstream]` (idempotent: skip if already correct).
   - If no client matches: `POST /control/clients/add` with `name=device.slug`, `ids=device.mac`, `upstreams=[sinkhole_upstream]`, `use_global_settings=True`, `use_global_blocked_services=True`, `filtering_enabled=False`. (This is the auto-register-on-first-lock path; a separate operator-driven `push` for unmanaged devices comes later, after the plugin actions/panels surface from ADR-017 lands.)
4. **Unblock leg (`scope.locked = False`)**: for each remaining device:
   - If a client exists with `upstreams == [sinkhole_upstream]`, `POST /control/clients/update` with `data.upstreams = []`. Idempotent.
   - If no client exists, no-op (don't auto-register on the unblock leg — there's nothing to clear).
5. Return `ReconcileResult.ok()` on success. Catch `httpx.HTTPError` / non-2xx and return `ReconcileResult.error(msg)` with a short, redacted message (no creds in the message). The runtime audits the failure as `plugin.reconcile_failed`.

**Endpoints used** (all under `${url}/control/`):
- `GET /clients` — list
- `POST /clients/add` — `{name, ids, upstreams, use_global_settings, use_global_blocked_services, filtering_enabled}`
- `POST /clients/update` — `{name: <existing-name>, data: <full client>}`

**Tests**: unit tests with `respx` mocking `httpx.AsyncClient` (or hand-rolled mock transport — `respx` would need to be added as a dev dep, check first). Cover:
- Block leg: device with MAC → POST `/clients/add` with sinkhole upstream when client missing; POST `/clients/update` when client exists with wrong upstream; no call when already sinkholed (idempotent).
- Unblock leg: POST `/clients/update` to clear upstream when sinkholed; no-op when client missing or already cleared.
- Devices with empty `mac` are skipped silently.
- Unmanaged devices in `scope.devices` are ignored.
- AdGuard 5xx → `ReconcileResult.error` (not exception).
- Multi-MAC device: one `add` call, all MACs included in `ids`.

Test file location: prefer `plugins/adguard/tests/test_plugin.py` to keep it co-located, mirroring the convention used elsewhere (verify against repo first; if there's no precedent, fall back to `core/tests/test_adguard_plugin.py`).

**No CLI changes.** `curfew plugin assign adguard --config '{...}'` already works through the generic plugin assignment surface; no plugin-specific CLI in this PR.

### Side-quest: ADR-017 (schema-driven plugin UI)

Append a new ADR to `docs/DECISIONS.md` capturing:
- **Decision**: future plugin GUI extensions are schema-driven. Plugins declare *actions* (named methods + result types) and *panels* (typed read-only data) in Python; runtime exposes them at `POST /v1/plugins/{type}/{instance_id}/actions/{name}` and `GET /v1/plugins/{type}/{instance_id}/panels/{name}`. The SPA discovers them via `GET /v1/plugins/types` and renders generic widgets (buttons for actions, tables/key-value lists for panels). Plugin authors write zero JS.
- **Rejected**: plugin-shipped JS bundles (Backstage / VS Code style) — too much coupling for homelab scale; plugin authors shouldn't need to learn React. Server-rendered HTML islands embedded in the SPA — inherits MPA downsides without escaping the SPA. Going back to MPA — doesn't fix the problem; you'd still need a contribution mechanism.
- **Trade-off accepted**: plugin authors are constrained to the widgets the SPA implements. New UI shapes require coordinated SPA + SDK work. For the realistic plugin set (AdGuard, smart-plug, router-acl, tailscale), the widget set is small (one table panel + a couple of action buttons each).
- **Status**: not implemented. AdGuard, smart-plug etc. ship headless until this lands; operators use CLI/API.
- **Why a kernel commitment**: deciding the contribution shape now prevents the alternative (plugin-shipped JS) from accidentally creeping in via the first plugin author who needs a button.

This ADR can land in either PR — it's small and self-contained. Putting it in PR 1 keeps PR 2 focused on AdGuard.

## Critical files

- `core/curfew/plugin.py` — `Scope`, new `DeviceRef`
- `core/curfew/plugin_runtime.py` — `dispatch_for_user`, `safety_net_resync` populate `Scope.devices`
- `core/curfew/models.py` — `Device` (read-only reference)
- `plugins/reftest-plugin/plugin.py` — extend to assert `scope.devices`
- `plugins/adguard/manifest.toml` — new
- `plugins/adguard/plugin.py` — new
- `plugins/adguard/tests/test_plugin.py` — new
- `docs/DECISIONS.md` — append ADR-017
- `core/tests/test_plugin_runtime.py` — assert `Scope.devices` populated
- `api/tests/test_plugins_reconcile.py` — assert reftest sees devices

## Verification

**Per-PR `just check`**: the full battery (lint + mypy + tests + web tests + build) must pass on each PR independently.

**Live smoke (PR 2, end-to-end against a real AdGuard Home)**:
1. Stand up AdGuard Home locally (docker; admin user + password set).
2. Start curfew with `ADGUARD_PASSWORD=...` exported.
3. `curl POST /v1/plugins '{"type":"adguard","config":{"url":"http://localhost:8080","username":"admin","password_env":"ADGUARD_PASSWORD"}}'`
4. Create a managed user `kid1`, add a device with a MAC.
5. `POST /v1/users/kid1/lock` → confirm in AdGuard's "Clients" UI that a client appears for that MAC with upstream `0.0.0.0`. Confirm DNS resolution from a client with that MAC is sinkholed.
6. `PATCH /v1/users/kid1 {"managed": false}` (exercises the unmanaged-implies-unlocked invariant from PR #27 too) → AdGuard client's upstream returns to `[]`.
7. Audit log shows `plugin.reconcile_failed` only if AdGuard was unreachable; otherwise silent.

If a real AdGuard isn't handy, skip the live smoke; unit tests with mocked httpx are the substantive coverage.

## Out of scope (named so we don't drift)

- Plugin actions/panels SDK and SPA generic plugin renderer (ADR-017's implementation; separate, larger work).
- `curfew adguard push` bulk-sync command (waits for the plugin actions surface).
- AdGuard "Allowed/Disallowed clients" management — we use per-client upstream sinkholing only, matching the original script.
- IP-based AdGuard client IDs — `Device` doesn't store IPs; MAC-only is sufficient.
- Cascade-deleting AdGuard clients when a curfew Device is removed. Operator cleans those up out of band (or via the future `push`/`purge` actions).
- Crash-isolating plugins from curfew-core (ADR-013 trade-off; unchanged here).
