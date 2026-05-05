# curfew — Plan

CLI- and (later) web-driven tool to manage screentime across the household. Built around an **API-first, plugin-based** architecture: a small dockerized core service holds state and exposes an HTTP API; *plugins* implement specific enforcement methods (Windows PC lockout, DNS sinkhole, smart plugs, router ACLs, etc.). New methods are added by writing new plugins. The CLI and a future GUI are both thin clients of the same API.

## Goals

1. **Modular** — each enforcement method is an independent plugin.
2. **API-first** — the core HTTP API is the contract. CLI, GUI, plugins, and any future surface are all clients.
3. **Docker-hosted core** — the user runs the core as a docker compose stack alongside other homelab services.
4. **Pull-based enforcement** — plugins reconcile against state on a timer. Self-heals through reboots, sleep, and network changes.
5. **Well-tested** — core, CLI, plugin SDK, and individual plugins all have automated tests in CI.
6. **Kernel-then-features progression** — build a complete kernel up front; layer features on top in any order.

## Architecture & topology

```
+------------------+     +-----------------------+     +-----------------------------+
| Operator machine |     | Homelab host          |     | Each managed PC (Windows)   |
| (your laptop)    |     | (docker compose)      |     |                             |
|                  |     |                       |     |                             |
|  $ curfew lock   | --> |  curfew-core (FastAPI)| <-- |  agent.ps1                  |
|     kid1         |     |  state.sqlite (volume)|     |  Scheduled Task, every 1min |
|                  |     |  behind Traefik       |     |  applies icacls + registry  |
|  curfew CLI      |     |                       |     |                             |
|  (HTTP client)   |     |  + future: web UI     |     |  installed once via         |
|                  |     |    served by same     |     |  install-agent.ps1          |
|                  |     |    container          |     |                             |
+------------------+     +-----------------------+     +-----------------------------+

   talks to API           the only "always-on"          per-device install — bootstrap
   over HTTPS             component. one container.    once, self-updates after.
```

Three places code physically lives:

- **Operator machine** — CLI binary/script. Stateless. Each invocation is short-lived. Talks to the core's API over HTTPS.
- **Homelab host** — one docker compose stack: a single FastAPI container serving the API, fronted by your existing Traefik. Persists `state.sqlite` in a docker volume.
- **Each managed PC** — small PowerShell agent + a Windows Scheduled Task. Installed once via a bootstrap script. The agent fetches state from the API on each tick and reconciles local enforcement.

## Plan shape: kernel + features

The plan is *not* a feature timeline (V1 → V5+). It's two sections:

- **The kernel** is the set of architectural primitives every feature depends on. It is built once, complete and tested. Features stack on top without reshaping the kernel.
- **Features** are unordered. Implement whatever's most needed when. Manual lock can come before schedule lock; adguard can come before windows-pc; the GUI can come before budgets. The kernel doesn't notice.

This shape exists because the alternative — feature-phased V1 → V5+ — keeps creating "we'll add it in V2" commitments that turn into refactors. The kernel is bigger up front; everything after it is genuinely additive.

---

# The kernel

## Storage

A single store: **`state.sqlite`** in a docker volume. SQLModel for the schema, Alembic for migrations from day one. No additional config files in the architecture.

The schema is created in the initial migration with all fields the system will ever need — even fields no rule reads yet. New features may add tables (e.g. `usage_minutes` when the budget feature lands), but they don't reshape existing tables.

### Tables

| Table | Purpose |
|-------|---------|
| `users` | name (PK), role, managed (bool, default true), target_apps (JSON list) |
| `devices` | name (PK), owner (FK→users, nullable), type, os, mac (JSON list), managed (bool) |
| `apps` | name (PK), exe_paths (JSON list), process_names (JSON list), urls (JSON list) |
| `device_agents` | (device PK, type, config JSON) — the agent type installed on this device (one per device), with config (e.g. which Windows local user account to ACL) |
| `agent_instances` | device (PK, FK→devices), last_heartbeat (nullable), last_seen_version. Runtime state; one row per `device_agents` row, created and deleted in the same transaction. `last_heartbeat IS NULL` means the agent has never reported in (assigned but not yet bootstrapped). The kernel records this fact; it doesn't interpret stale heartbeats as an alert (a powered-off device looks the same as a broken agent without independent reachability data — see the Reachability monitoring feature) |
| `agent_tokens` | token_hash (PK), device, created_at, revoked_at — bearer per device, used by the agent on that device to authenticate |
| `plugins` | (type, instance_id) PK; config JSON; governs JSON list (user names, or `["*"]` for all managed users); paused bool. In-core plugins are discovered from any directory listed in `CURFEW_PLUGINS_DIRS`; instance_id is empty by default and only set when multiple instances of the same type are needed |
| `user_locks` | user (PK, FK→users), manual_lock (bool), set_at, set_by |
| `audit_log` | id (PK), actor, action, target, payload (JSON), occurred_at |
| `manifests` | type (PK), version, sha256, agent_url — versioned **agent** artifacts (plugins are not distributed this way; they're files on disk) |
| `settings` | Single-row table (id=1 enforced). Operator-tunable runtime knobs (tick rates, retention windows). Initial migration creates the row with defaults; feature migrations add columns. See `## Configuration and settings`. |

### Data model — concepts

**Users** are anyone in the household curfew touches. Two independent axes: `role` (`member` / `manager` / `admin`) determines what a user can do; `managed` (bool) determines whether lock rules apply to them. Schedules and budgets attach to managed users — one user with one PC and one tablet shares one daily budget across both.

| Field | Why it matters |
|-------|----------------|
| `name` | Stable identifier used in CLI/API/UI. Short string (`kid1`), not a real name. |
| `role` | `member`, `manager`, or `admin`. Capabilities are cumulative: `member` has no operator powers; `manager` can lock/unlock any user with `managed: true` (including themselves and other managers); `admin` is everything `manager` is plus can edit users, devices, apps, plugin assignments, and agent manifests. Future auth/RBAC keys off this. |
| `managed` | Bool, defaults to `true`. Whether lock rules apply to this user. Independent of `role` — a teen `manager` could be `managed: true` (has lock control over siblings *and* their own rules apply); a houseguest could be `member, managed: false` (no powers, not subject to rules). When `false`, `GET /v1/users/{user}/status` always returns `{locked: false, reasons: []}` and the rule pipeline is skipped. Operators typically opt out (`--managed false`) for adult managers and admins, and for guests; a teen `manager` who's also subject to rules stays `managed: true`. |
| `target_apps` | Apps that get blocked when this user is in a locked state (manual lock today; out-of-schedule and budget-exhausted in later phases). References keys in `apps`. |

**Per-rule user config — decision deferred:** rules like schedule and budget will eventually need per-user configuration (a schedule expression, a budget value). Where that lives isn't yet decided. Options when the first such rule lands:

- **Columns on `users`** — e.g. `users.schedule`, `users.budget_minutes`. Simple; one query reads everything. Adds a column per new rule (migration); `users` becomes a junk drawer of rule fields over time.
- **Per-rule tables** — e.g. `user_schedules(user, expr, tz)`, `user_budgets(user, minutes, period)`. Cleanest separation; adding a rule is adding a table, no `users` migration. More joins.
- **Single JSON field** — e.g. `users.rule_config JSON`. Schemaless from SQL, validated per-rule by Pydantic. Adding a rule is zero schema change. Less queryable.

The kernel commits to none of the above; pick when the first non-kernel rule (likely schedule) actually needs to land. The same decision covers **today-only overrides** for schedules and budgets — extra-minutes-for-today, late-bedtime-tonight — which need a parallel storage shape (date-keyed, auto-expiring at day rollover).

**Devices** are physical things curfew (or a plugin) can act on.

| Field | Why it matters |
|-------|----------------|
| `name` | Used in CLI/UI and as part of plugin instance names (`windows-pc:gamingrig`). |
| `owner` | The user this device belongs to. For managed devices, also the user whose schedule/budget activity here debits. Null = shared device (governed as a group via the shared-device-lock feature when registered). |
| `type` | `pc | laptop | phone | tablet | console | tv`. Constrains which plugin types apply. |
| `os` | `windows | macos | linux | ios | android`. Selects per-device plugin variants. |
| `mac` | Network-layer identity, stable across IP changes. List, since a device commonly has multiple MACs (Wi-Fi + ethernet; randomized per network). Read by network-side plugins. |
| `managed` | Whether curfew governs this device at all. Lets devices be tracked for completeness without being put under policy (e.g. a manager's laptop tracked but not enforced). |

**Why people *and* devices, not just users-with-a-list-of-devices:**

- Schedules and budgets attach to **people**, not devices. One member, two devices, one shared budget.
- Plugin coverage is **per-device** — DNS for the phone, OS-level lock for the PC. Different plugins, same person.
- A device may change hands (hand-me-down PC) and its `owner` updates without rewriting policy.

**Out of scope** (deliberately excluded from the data model):

- VLAN / network segmentation — a network concern, not a screentime one.
- Static-vs-dynamic IP — irrelevant; we key on MAC.
- Hardware specs, purchase dates, warranty — asset-management territory.

## Lock status rule pipeline

The kernel computes lock status per scope. Two scopes ship in the kernel:

- **User scope** — `GET /v1/users/{user}/status` returns `{locked: bool, reasons: [...]}`. The OR of registered user-scope rules.
- **Device scope** — `GET /v1/devices/{device}/status` returns the same shape for a device. The OR of registered device-scope rules. Empty until any device-scope rule registers (the shared-device-lock feature is the first).

Rules implement a single interface, declare their scope at registration, and return zero or one reason. The kernel ships with **one rule: `manual_lock`** (user scope).

**Reasons are structured objects, not strings:** `{kind: "manual_lock"}`, `{kind: "out_of_schedule"}`, `{kind: "budget_exhausted", consumed: 120, budget: 90}`. Clients render `kind` and ignore unknown fields. Adding detail to a reason is a non-breaking change.

Every additional lock condition (schedule, budget, shared-device, future per-device locks) is a new rule registered into the right scope's pipeline. No API surface changes, no central if/else to edit.

## Two extension surfaces: agents and plugins

curfew has two ways to add enforcement, with different physics:

- **Agents** run **on a managed device** (Windows PC, Mac). They poll curfew-core because they can't be reached from the homelab (NAT, sleep, intermittent connectivity). One agent per device. Distributed via bootstrap install + versioned/hash-verified self-update. Agent code is **first-party only** — extending what an agent does means contributing to or forking the agent's source. There's no drop-in third-party extension model on the device side; "drop in your own enforcement code" only applies to plugins.
- **Plugins** run **inside curfew-core** as drop-in Python modules. They're called directly by the core when state changes (in-process function call, not HTTP). Distributed by dropping a folder into any directory listed in `CURFEW_PLUGINS_DIRS` (the bundled `plugins/` dir is the default; operators add more dirs to install third-party plugins).

Same goal — *given a user's lock state, make my surface reflect it* — but the mechanics differ.

## Agent lifecycle

One agent per device. The agent code is `windows-pc-agent`, `macos-pc-agent`, etc. — an installable program that runs on the device and polls curfew-core.

1. Operator installs the agent on a device (`curfew agent install windows-pc gamingrig --config '{"windows_user": "Kid1Local"}'`). This:
   - Inserts a row in `device_agents`.
   - Creates the matching `agent_instances` row in the same transaction.
   - Mints a device-scoped bearer token (one row in `agent_tokens`); returns the secret once.
   - Prints a bootstrap one-liner for the operator to run on the device.
2. Operator runs the bootstrap on the device. It fetches the agent code via the versioned manifest (`GET /v1/agents/{type}/manifest`), verifies SHA-256, installs, registers a scheduled task / launchd / cron job.
3. Agent heartbeats every tick (`POST /v1/devices/{device}/heartbeat`) carrying its last-known state hash. If the server's hash differs, the agent fetches `GET /v1/devices/{device}/state` and re-runs its reconciler. (See ADR-012.)
4. Core records `last_heartbeat` on each tick. `GET /v1/agents` returns `last_heartbeat` (and `last_heartbeat IS NULL` if the agent has never reported in yet — assigned but not bootstrapped). The kernel doesn't interpret this as an alert: a powered-off device looks the same as a broken agent without an independent presence signal. Real "device is on but agent isn't checking in" alerting is the Reachability monitoring feature.
5. Removal: `curfew agent uninstall gamingrig` → deletes the `device_agents` row → `agent_instances` cascade-deletes → outstanding tokens revoked → next agent tick gets 401 → operator removes the local install.

**Drift = unenforced.** An agent that isn't heartbeating isn't enforcing. The database knows the user is locked; the device doesn't. Drift surfaces on `GET /v1/agents` for the operator to investigate. Optional fail-closed agent behaviour (auto-lock on disconnect) is a feature on top — see "Auto-lock on disconnect."

## Plugin lifecycle

Plugins are Python modules dropped into any directory listed in `CURFEW_PLUGINS_DIRS` — a colon-separated list of paths (PATH-style). The default is the bundled `plugins/` directory inside the curfew-core image; operators add more entries to install third-party plugins (e.g. `CURFEW_PLUGINS_DIRS=/usr/lib/curfew/plugins:/etc/curfew/plugins`). Directories are scanned in order; if the same `type` is declared in two of them, the later entry wins (so operator-mounted dirs can override shipped plugins). Each subdirectory is one plugin type.

```
plugins/
├── adguard/
│   ├── manifest.toml      # type, name, version, config_schema, description
│   ├── plugin.py          # subclasses Plugin, implements reconcile()
│   └── requirements.txt   # optional pip deps
├── smart_plug/
│   ├── manifest.toml
│   └── plugin.py
└── wyze/                  # operator-authored
    ├── manifest.toml
    ├── plugin.py
    └── requirements.txt
```

1. **Discovery** (at curfew-core startup): scan each directory in `CURFEW_PLUGINS_DIRS` in order. For each subdirectory: read `manifest.toml`, optionally `pip install -r requirements.txt` into curfew-core's shared Python environment, import `plugin.py`, find the class subclassing `Plugin`, register it under its `type` name.
2. **Assignment** (operator): `curfew plugin assign adguard --config '{"url": "..."}' --governs '["*"]'`. Validates config against the plugin's Pydantic schema, inserts a row in `plugins`, instantiates the plugin in memory.
3. **Reconciliation** (event-driven): when state changes for a user the plugin governs (lock toggled, schedule fired, etc.), curfew-core calls the plugin's `reconcile()` method directly. In-process function call; latency is microseconds.
4. **Safety-net resync** (slow tick, default every 5 minutes): curfew-core walks all governed users and calls `reconcile()` on each registered plugin instance for each governed user. Catches missed events from restarts.
5. **Pause / unpause**: `curfew plugin pause adguard` flips a `paused` flag. The core stops calling reconcile until unpaused; the instance stays loaded and config is preserved.
6. **Removal**: `curfew plugin unassign adguard` deletes the row, drops the in-memory instance.

Plugins don't heartbeat — they're in-process; their liveness is the core's. Plugins don't have tokens — there's no network boundary to authenticate. Adding new plugin code (a new subdirectory in one of the `CURFEW_PLUGINS_DIRS`) requires a curfew-core restart to pick up; toggling existing plugins doesn't.

Plugins share curfew-core's Python environment. Their `requirements.txt` deps are installed into that one shared env at startup — Python doesn't really support per-module dependency isolation in-process. Conflicting versions across plugins are the operator's problem to resolve (typically: pick a compatible version range, or vendor inside the plugin). For curfew's intended audience this is fine; the realistic plugin set targets common HTTP libraries and overlaps cleanly.

See [PLUGINS.md](PLUGINS.md) for the plugin author's guide and [AGENTS.md](AGENTS.md) for the agent author's / operator's guide.

## Agent update integrity

Plugins running on devices fetch their agent code from the core. Updates are versioned and hash-verified:

- `GET /v1/agents/{type}/manifest` → `{ version, sha256, url }`.
- Agent compares the manifest's version to its installed version. If different: fetch `url`, verify SHA-256 matches the manifest, swap atomically.
- Update check runs on a slow tick (default hourly); state polling stays on the fast tick (default 60s). Failure to fetch a manifest never blocks state polling.
- Operator publishes a new agent version by uploading an artifact and rotating the `manifests` row.

Future tightening (future feature, not the kernel): manifest signing with a long-lived key embedded in the bootstrap. The kernel design accommodates this without reshape.

## SDKs

Two distinct SDKs because agents and plugins are different shapes:

### Agent SDK (for on-device agents)

Polling-loop SDK with two flavors. An agent author writes a reconciler; the SDK runs the loop, the heartbeat, the manifest fetch, the hash verification, and the error reporting.

- **`curfew_agent_sdk_python`** — for `macos-pc` and any future Linux/macOS agent.
- **`curfew-agent-sdk-powershell`** — for `windows-pc` and any future Windows agent.

Both expose:

- Polling loop with tick rate driven by the heartbeat response
- Device-level heartbeat dispatch with retry/backoff and **hash-based change detection** (per ADR-012)
- Versioned + hash-verified self-update (per ADR-009)
- Error reporting back to core (via heartbeat payload)
- Config bootstrap (device name, API URL, token)

**Hash-based change detection.** Each heartbeat carries the agent's last-known state hash; the server returns its current hash plus a small set of immediate-effect settings (`agent_tick_seconds`, etc.). Match → no work this tick. Mismatch → agent fetches `GET /v1/devices/{device}/state` (lock status, per-device config, target apps, relevant app catalog entries) and re-runs the reconciler. Heartbeats stay tiny; state pulls happen only when something actually changed.

### Plugin SDK (for in-core plugins)

Small Python helper. Not a polling loop — there's no loop, the core calls the plugin directly. Provides:

- A `Plugin` base class with a `reconcile(scope)` method to override
- Manifest validation helpers (`config_schema` resolution against Pydantic models)
- Common types: `LockStatus`, `Reasons`, `ReconcileResult`
- Optional helpers for HTTP clients with timeout/retry, since plugins almost always call out to some external service

A plugin is a Python file. Importable shape:

```python
from curfew.plugin import Plugin, ReconcileResult
from pydantic import BaseModel

class Config(BaseModel):
    url: str

class AdGuardPlugin(Plugin):
    def __init__(self, config: Config):
        self.config = config

    async def reconcile(self, scope) -> ReconcileResult:
        if scope.locked:
            await self._add_rules(scope.user, scope.target_apps)
        else:
            await self._clear_rules(scope.user)
        return ReconcileResult.ok()
```

The plugin SDK is much smaller than the agent SDK because most of the agent SDK's work (polling, heartbeating, self-update) doesn't apply to in-process plugins.

### Reference test consumers

The kernel ships two no-op test consumers:

- **`reftest-agent`** — exercises the agent SDK end-to-end (bootstrap, manifest fetch, heartbeat, state pull, reconcile).
- **`reftest-plugin`** — exercises the plugin SDK end-to-end (discovery, instantiation, reconcile call).

Both live in the test suite; they're what the kernel acceptance test runs against, so the kernel can be tested without depending on any feature-level agent or plugin.

### Which SDK each consumer uses

| Consumer | Kind | Where it runs | SDK |
|---|---|---|---|
| `windows-pc` | agent | Windows PC | `curfew-agent-sdk-powershell` |
| `macos-pc` | agent | macOS device | `curfew_agent_sdk_python` |
| `adguard` | plugin | in-core (curfew-core process) | plugin SDK |
| `smart-plug` | plugin | in-core | plugin SDK |
| `router-acl` | plugin | in-core | plugin SDK |
| `tailscale-acl` | plugin | in-core | plugin SDK |
| `reftest-agent` | agent (test) | test harness | `curfew_agent_sdk_python` |
| `reftest-plugin` | plugin (test) | in-core | plugin SDK |

## Configuration and settings

The curfew-core API service has two surfaces for tunable values:

- **Config** is boot-time and immutable for the life of the process. Comes from env vars, an optional `.env`, and `config.json`. Used for values needed before the database is open (db path, listen address, root token).
- **Settings** are runtime-mutable, stored in the database, edited via the API/CLI. No restart required. Used for operational knobs (tick rates, retention windows, thresholds).

Plugin agents have their own bootstrap config (`agent.config` per instance — see "Plugin instance lifecycle"). This section is about the curfew-core API service.

### Config sources (boot-time)

Loaded in order; later wins:

1. Built-in defaults (in code).
2. `config.json` (path: `CURFEW_CONFIG_PATH`, default `/etc/curfew/config.json` in the docker image, `./config.json` locally).
3. `.env` file in the working directory (loaded via python-dotenv, populates env before consumption).
4. Process environment variables (highest precedence).

What lives in config:

| Var | Purpose |
|---|---|
| `CURFEW_DB_PATH` | `state.sqlite` location |
| `CURFEW_ROOT_TOKEN` | operator bearer; env-only, never in `config.json` |
| `CURFEW_LISTEN_HOST` / `CURFEW_LISTEN_PORT` | bind address for the FastAPI server |
| `CURFEW_AGENT_BASE_URL` | public URL agents call back to (Traefik-fronted hostname) |
| `CURFEW_PLUGINS_DIRS` | colon-separated list of directories scanned for in-core plugins at startup. Default: the bundled `plugins/` directory in the curfew-core image. Operators extend by appending paths (e.g. `CURFEW_PLUGINS_DIRS=/usr/lib/curfew/plugins:/etc/curfew/plugins`). Later entries override earlier on duplicate `type`. |
| `CURFEW_LOG_LEVEL` | `debug` / `info` / `warn` / `error` |
| `CURFEW_CORS_ORIGINS` | allowed origins for the future GUI |

Convention: secrets live in env (or `.env` for local dev) so `config.json` can be checked in or templated. Implemented with Pydantic v2 `BaseSettings` — schema-validated at boot, so a bad config fails fast rather than producing surprising behaviour.

### Settings (runtime-mutable)

Single-row `settings` table with typed columns. The initial migration creates the row with defaults; feature migrations add columns.

| Setting | Default | What it controls |
|---|---|---|
| `agent_tick_seconds` | 60 | How often agents poll the core (heartbeat + state-hash check) |
| `manifest_tick_seconds` | 3600 | How often agents check for code updates |
| `plugin_resync_seconds` | 300 | Safety-net resync interval — core walks all governed users and re-calls each plugin's reconcile |
| `audit_retention_days` | 90 | Rolling window before audit rows are pruned |

API: `GET /v1/settings` (read all), `PATCH /v1/settings` (update one or more). CLI: `curfew setting list | get | set`. Writes go through the audit log.

## Authentication

- **Plugin auth**: per-instance bearer tokens (ADR-006). Hashed at rest. Read scope is full state in the kernel; future tightening to scoped reads is a feature on top.
- **Operator auth**: V1 uses a single **root bearer token** (`CURFEW_ROOT_TOKEN` env var; see "Configuration and settings"), distinct from the `admin` user role (which is data-only in V1 — no way for a user with `role: admin` to authenticate yet). Per-user role-based auth (sessions tied to user records) is a feature on top of the kernel; the API surface is shaped to accept it without renames.

## Audit log

Every API write goes through a single audit middleware that records `(actor, action, target, payload, occurred_at)` to the `audit_log` table. The kernel writes the log; reads via API are a feature on top.

## OpenAPI contract

Pydantic v2 schemas everywhere. OpenAPI auto-emitted at `/v1/openapi.json`. CLI types and (future) GUI types generated from the same source — renames become a build error, not a runtime surprise.

## API surface (kernel)

```
User / device / app CRUD
  GET    /v1/users                              list
  GET    /v1/users/{user}                       fetch
  POST   /v1/users                              create
  PATCH  /v1/users/{user}                       update
  DELETE /v1/users/{user}                       delete

  GET    /v1/devices                            list
  GET    /v1/devices/{device}                   fetch
  POST   /v1/devices                            create
  PATCH  /v1/devices/{device}                   update
  DELETE /v1/devices/{device}                   delete

  GET    /v1/apps                               list
  GET    /v1/apps/{app}                         fetch
  POST   /v1/apps                               create
  PATCH  /v1/apps/{app}                         update
  DELETE /v1/apps/{app}                         delete

Agents (per-device extension surface)
  GET    /v1/agents                             list installed agents with last_heartbeat (consumer interprets staleness)
  POST   /v1/devices/{device}/agent             body { type, config }; installs an agent on this device, mints a bearer, returns { token: { id, secret }, bootstrap }
  DELETE /v1/devices/{device}/agent             uninstall the agent (deletes device_agents + agent_instances rows, revokes tokens)
  POST   /v1/devices/{device}/heartbeat         body { state_hash, agent_version }; returns { state_hash, agent_tick_seconds, ... }
  GET    /v1/devices/{device}/state             full state for this device's agent: scoped lock status, agent config, target apps, relevant app catalog entries
  POST   /v1/devices/{device}/activity          body { user, occurred_at, ... }; no-op until budget rule registered
  POST   /v1/devices/{device}/tokens            mint an additional bearer for this device; returns { id, secret }
  GET    /v1/devices/{device}/tokens            list active token ids (no secrets)
  DELETE /v1/devices/{device}/tokens/{id}       revoke

Plugins (in-core extension surface)
  GET    /v1/plugins                            list assigned plugin instances + paused/governs/config
  GET    /v1/plugins/types                      list discovered plugin types from CURFEW_PLUGINS_DIRS + their config_schema
  POST   /v1/plugins                            body { type, instance_id?, config, governs }; assigns a plugin
  PATCH  /v1/plugins/{type}                     update config / governs / paused (use {type}:{instance_id} when not the default)
  DELETE /v1/plugins/{type}                     unassign

Locks
  POST   /v1/users/{user}/lock                  manual lock
  POST   /v1/users/{user}/unlock                manual unlock

Lock status
  GET    /v1/users/{user}/status                { locked: bool, reasons: [{kind, ...}] }
  GET    /v1/devices/{device}/status            { locked: bool, reasons: [{kind, ...}] } — empty pipeline in kernel

Agent code distribution
  GET    /v1/agents/{type}/manifest             { version, sha256, url } — what the bootstrap fetches
  GET    /v1/agents/{type}/{version}            agent artifact (the .ps1 / .py / .sh package)
  POST   /v1/agents/{type}/versions             publish a new agent version: body { version, artifact }; rotates the manifest pointer to it

Settings
  GET    /v1/settings                           read all runtime-mutable settings
  PATCH  /v1/settings                           update one or more (audited)

Admin
  GET    /v1/admin/snapshot                     full system snapshot (debug; not the primary read path)
  GET    /v1/health                             liveness probe
```

## CLI shape (kernel)

```
curfew user             add | list | show | edit | rm
curfew device           add | list | show | edit | rm
curfew app              add | list | show | edit | rm

curfew agent            install <type> <device> | uninstall <device> | list
curfew agent token      mint <device> | list <device> | revoke <device> <id>
curfew agent publish    <type> <version> <file>             # publish a new version of agent code

curfew plugin           assign <type> | unassign <type> | pause <type> | unpause <type> | list | types

curfew setting          list | get | set
curfew lock             <user>
curfew unlock           <user>
curfew status                                                # human-readable summary

# Convention: every list/show/status command accepts --json for machine-readable output.
```

## Repository layout

```
curfew/
├── src/
│   ├── curfew/                        # shared library — models, schemas, rule interface, Plugin base class
│   ├── curfew_api/                    # FastAPI service
│   │   └── migrations/                # Alembic
│   ├── curfew_cli/                    # CLI — thin HTTP client
│   ├── curfew_agent_sdk_python/       # Python agent SDK (polling, heartbeat, self-update)
│   └── curfew-agent-sdk-powershell/   # PowerShell agent SDK module (parallel, for Windows)
├── agents/
│   ├── windows-pc/
│   │   ├── agent.ps1                  # uses the PowerShell agent SDK
│   │   ├── install-agent.ps1          # bootstrap installer
│   │   └── tests/                     # Pester tests
│   └── macos-pc/
│       ├── agent.py                   # uses the Python agent SDK
│       ├── install-agent.sh           # bootstrap installer
│       └── tests/
├── plugins/                           # in-core plugins — shipped with curfew
│   ├── adguard/
│   │   ├── manifest.toml
│   │   ├── plugin.py
│   │   └── requirements.txt
│   ├── smart_plug/
│   │   ├── manifest.toml
│   │   └── plugin.py
│   └── ...                            # router-acl, tailscale-acl, etc.
├── tests/
│   ├── unit/                          # models, schemas, rule pipeline, SDKs
│   ├── api/                           # FastAPI TestClient integration tests
│   ├── cli/                           # CLI against a mocked or real API
│   ├── reftest_agent/                 # reference test agent (Python agent SDK consumer)
│   ├── reftest_plugin/                # reference test plugin (manifest.toml + plugin.py)
│   └── e2e/                           # docker-compose-up + CLI roundtrip + both reftests
├── docker/
│   ├── Dockerfile
│   └── compose.yml
├── docs/
│   ├── PLAN.md
│   ├── DECISIONS.md
│   ├── AGENTS.md                      # how to author and operate agents
│   └── PLUGINS.md                     # how to author and operate plugins
├── .github/workflows/
│   ├── ci.yml                         # ruff + mypy + pytest (Linux)
│   └── windows.yml                    # Pester (Windows runner)
├── pyproject.toml
└── README.md
```

## Testing strategy

| Layer | Framework | What's covered |
|-------|-----------|----------------|
| Schema models | pytest + in-memory SQLite | CRUD round-trips, schema validation, migrations apply forward, FK/uniqueness behaviour. ≥90% coverage. |
| Config loading | pytest | Precedence (env > .env > config.json > defaults); bad values refuse to boot; secrets never read from `config.json`. |
| Rule pipeline | pytest | Rule registration, OR composition, reasons aggregation, behaviour with zero rules. |
| Agent contract | pytest with reftest_agent | Reference test agent exercises the full agent contract end-to-end; living documentation. |
| Plugin contract | pytest with reftest_plugin | Reference test plugin exercises the discover-instantiate-reconcile flow; living documentation. |
| Agent SDK (Python) | pytest | Polling loop, heartbeat retry, state-hash change detection (matching → no-op; mismatch → pull `/state`), manifest fetch, hash mismatch refusal. |
| Agent SDK (PowerShell) | Pester | Same coverage, on the Windows runner. |
| Plugin SDK | pytest | Plugin discovery across multiple `CURFEW_PLUGINS_DIRS` (including override-on-duplicate-type), manifest validation, config schema enforcement, reconcile error containment. |
| API | pytest + FastAPI TestClient | Every endpoint: happy path, auth failures, validation errors, idempotency. |
| Agent update flow | pytest + Pester | Manifest endpoint, hash verification, atomic swap, rollback on failed swap. |
| CLI | pytest + httpx mocking | Each command, exit codes, output formatting. |
| Audit log | pytest | Every API write produces a row; payloads are structured. |
| End-to-end | pytest + docker compose | Spin up the stack, run CLI, observe an agent heartbeat (and last_heartbeat advancing), observe a plugin reconcile being called on lock. |

CI runs on every push:
- **Linux runner**: ruff + mypy + pytest (unit, API, CLI, e2e with docker compose).
- **Windows runner**: Pester for the PowerShell agent SDK + windows-pc agent + installer smoke test.

Pre-commit hooks for lint/format. Type hints required (`mypy --strict` for the core).

## Acceptance: kernel done

The kernel is "done" when both extension surfaces work end-to-end. Two reference test consumers live in the test suite — `reftest_agent` (a no-op agent SDK consumer that writes a sentinel file when locked) and `reftest_plugin` (a no-op in-core plugin that writes a different sentinel file when its `reconcile` is called) — and the kernel acceptance exercises both.

**Agent path:**

1. CLI creates a user (`curfew user add kid1 --role member`) and a device (`curfew device add gamingrig --owner kid1 --type pc --os windows`).
2. CLI publishes the reference agent's first version (`curfew agent publish reftest 1.0.0 ./reftest_agent.tar`).
3. CLI installs the reference agent (`curfew agent install reftest gamingrig --config '{}'`); verify the `device_agents` and `agent_instances` rows are created in the same transaction; CLI prints the secret once and the bootstrap one-liner.
4. The reference agent runs the bootstrap. It fetches the manifest, verifies SHA-256, installs.
5. Agent heartbeats; `GET /v1/agents` shows it healthy.
6. Stop the agent; `GET /v1/agents` shows the agent's `last_heartbeat` no longer advancing (interpretation is the consumer's job — the kernel just records).
7. CLI locks the user (`curfew lock kid1`); the manual_lock rule fires; `GET /v1/users/kid1/status` returns `{locked: true, reasons: [{kind: "manual_lock"}]}`.
8. State-hash mismatch on next heartbeat → agent pulls `/state` → reads lock status → writes its sentinel file.
9. CLI unlocks; agent clears its sentinel.
10. CLI removes the agent (`curfew agent uninstall gamingrig`); rows are deleted, outstanding tokens revoked, next heartbeat returns 401.

**Plugin path:**

11. Drop `reftest_plugin/` into a directory in `CURFEW_PLUGINS_DIRS`; restart curfew-core. `GET /v1/plugins/types` lists `reftest_plugin` as discovered.
12. CLI assigns the plugin (`curfew plugin assign reftest_plugin --config '{}' --governs '["kid1"]'`).
13. CLI locks `kid1` again; the core directly calls `reftest_plugin.reconcile()` (in-process); the plugin writes its sentinel.
14. CLI unlocks; reconcile is called again; the plugin clears its sentinel.
15. CLI pauses the plugin (`curfew plugin pause reftest_plugin`); subsequent state changes do not trigger reconcile.
16. CLI unassigns; the in-memory instance is dropped.

**Cross-cutting:**

17. `audit_log` contains a structured row for every API write in the entire sequence.

windows-pc and adguard land as the first real feature implementations of each surface, immediately after the kernel passes.

---

# Features

Features stack on top of the kernel without reshaping it. **Order is arbitrary** — implement whichever is most needed when. The list below is roughly ordered by logical dependency (manual lock first because it's the kernel's reference rule; budget after schedule because both build on the same rule machinery), but the order isn't load-bearing.

## Manual lock rule (kernel reference)

The first rule registered (user scope). Reads `user_locks.manual_lock`; returns `{kind: "manual_lock"}` when set. Ships with the kernel as the proof-of-concept rule.

## Schedule lock rule

A new user-scope rule. Reads the user's recurring schedule (storage TBD per the data-model deferred decision); evaluates against current time + timezone; returns `{kind: "out_of_schedule", schedule: "..."}` when out of window. CLI: `curfew schedule <user> "<expr>"`.

**Today-only overrides.** Operator can extend or replace today's schedule for a single user (e.g. "tonight let kid1 stay up until 22:00") without changing the recurring schedule. Likely a small `schedule_overrides(user, date, expr)` table that the rule consults first and falls back to the recurring schedule when no override exists for today; overrides expire when the day passes. CLI: `curfew schedule <user> --today "<expr>"`.

Tests: expression parsing, time-zone handling, transitions across midnight/DST, today-override precedence and expiry.

## Budget lock rule + activity ingestion

Adds `usage_minutes(user, day, minutes)` table via Alembic migration. Agents call `POST /v1/devices/{device}/activity` with `{user, occurred_at}` when they observe recent user input — the kernel endpoint that was a no-op now writes to `usage_minutes`. A new user-scope rule returns `{kind: "budget_exhausted", consumed: 120, budget: 90}` when the user's recurring budget is consumed for the period. Resets daily/weekly. CLI: `curfew budget <user> <minutes>`.

**Today-only bonus.** Operator can grant extra minutes for a single day (e.g. "give kid1 30 more minutes today") without changing the recurring budget. Likely a `budget_overrides(user, date, extra_minutes)` table; the rule adds the override to the recurring allowance when computing remaining minutes for that day, and the override expires when the day passes. CLI: `curfew budget <user> --today +30`.

## Shared-device lock + `--shared` operations

A new **device-scope rule** — the first to register into the device-scope pipeline. Adds a device-level `shared_lock` flag (or row). The rule reads it for any device with `owner = NULL` and returns `{kind: "shared_lock"}` when set. CLI: `curfew lock --shared` and `curfew unlock --shared`. Endpoints: `POST /v1/shared/lock` / `unlock`. Plugins on shared devices read `GET /v1/devices/{device}/status`.

## Per-device app overrides

Adds a `device_app_overrides(device, app, exe_paths, process_names, urls)` table. Resolution path in the core: when a plugin reads the lock status for a device, the global app entry is overridden by the device's row if present. **Replace** semantics — override list replaces global list, not merge.

## Agents

On-device agent implementations. Each is a new agent type — a published agent artifact + a bootstrap installer. The agent SDK handles polling, heartbeating, and self-update; the agent author writes the reconciler.

- **`windows-pc`** (`curfew-agent-sdk-powershell`) — first agent; stress-tests the agent contract. NTFS deny-execute on configured exe paths + Chrome/Edge `URLBlocklist` registry policy. Kills matching running processes.
- **`macos-pc`** (`curfew_agent_sdk_python`) — same primitives translated for macOS.

Future: `linux-pc`, embedded-device agents, etc.

## Plugins (in-core implementations)

Drop-in Python plugins shipped with curfew (in `plugins/` in the repo, the default entry in `CURFEW_PLUGINS_DIRS`). Each is a folder with `manifest.toml` + `plugin.py`. Operator-authored third-party plugins live in any additional directory the operator adds to `CURFEW_PLUGINS_DIRS` — same shape, different origin.

- **`adguard`** — first in-core plugin; stress-tests plugin discovery + the resync path. DNS sinkhole via AdGuard Home REST API.
- **`smart-plug`** — Tasmota/Kasa power control.
- **`router-acl`** — UniFi/OPNsense API.
- **`tailscale-acl`** — gate egress for managed devices on the tailnet.

See [PLUGINS.md](PLUGINS.md) for how to author a new plugin.

## Auto-lock on disconnect (fail-closed agents)

Adds a `failclosed_after_seconds` setting (default `0` = disabled). When non-zero, any agent that hasn't successfully fetched state for that many seconds invokes its reconciler with `{locked: true, reasons: [{kind: "failclosed", since: ...}]}` regardless of last-known state. Closes the gap between "locked in the database" and "enforced on the device" while the agent is offline.

Implementation lives in the agent SDK — both Python and PowerShell flavours track time-since-last-successful-fetch and trip the fail-closed reconciler when they cross the threshold. Per-device opt-out or per-device threshold override (via `device_agents.config`) is a future tightening; the kernel ships a single global setting.

(Plugins don't need this — they're in-process; "disconnect" is meaningless for them.)

## Reachability monitoring

The kernel records `last_heartbeat` on each agent but doesn't interpret stale heartbeats as an alert — a powered-off PC looks identical to a broken agent without independent evidence the device is online.

This feature adds that independent evidence by probing devices on the homelab LAN (ARP via the device's `mac` field, ICMP ping, or watching DHCP leases — exact mechanism TBD when the feature lands). The core records reachability separately from heartbeats. The real alert state is then **device is reachable AND no recent heartbeat** — the kid PC is on, the agent isn't running, enforcement is bypassed.

Settings the feature would add (sketch):

- `reachability_probe_seconds` — how often to probe each device.
- `reachability_alert_threshold_seconds` — how long after a probe-vs-heartbeat divergence before alerting.

Notification channels (also part of this feature):

- **Outbound webhook** — `reachability_webhook_url` setting; the core POSTs when a probed-online device hasn't heartbeated in time. Operator pipes into Slack / email / whatever.
- **Polled query** — `GET /v1/agents?stale=true` (or similar) for an external cron.

Auto-lock on disconnect (above) is the agent-side counterpart and the primary mitigation: an agent that can't reach the core fails closed locally. Reachability monitoring is the operator-side signal — it tells you when an agent that *should* be running isn't.

(Plugins are in-process; if they crash, curfew-core itself is having a bad time and the homelab orchestrator notices that the container is unhealthy.)

## GUI

Web UI bundled into the same docker image. Reads `/v1/openapi.json` for types. Calls the same CRUD endpoints the CLI uses. No new architectural commitments.

## Agent manifest signing

Sign the manifest with a long-lived private key on the core. Embed the public key in the bootstrap. Agent verifies the signature before checking the hash. Drop-in over the existing manifest endpoint; closes the trust gap if the core itself is partially compromised.

## YAML import side tool

Optional. A CLI command (`curfew import yaml config.yaml`) or external script that reads a YAML config file and POSTs through the user/device/app CRUD endpoints. The architecture doesn't depend on it; this is a UX preference.

## Push (long-polling / SSE)

Almost certainly never needed. Pull-based at 60s tick is fine for screentime forever. Listed here for completeness; defer until there's a concrete use case that pull can't satisfy.

---

## Open questions

Implementation-level decisions still pending — none are kernel-architectural:

1. **Agent language for `windows-pc`** — pure PowerShell (zero deps on Windows) or bundled Python (cleaner state logic, requires runtime install)? *Lean: PowerShell for managed PCs to stay dependency-free.*
2. **Mid-session lock behaviour** — kill running processes immediately, force logoff, or just block future launches? *Lean: kill matching processes (with a config knob to opt out).*
3. **Initial app list** — Steam, Minecraft, YouTube definite; decide on Roblox, Discord, Twitch.
4. **CI runners** — public GitHub Actions (free Windows runner) vs. self-hosted on the homelab? *Lean: GitHub Actions until private code or speed becomes an issue.*
5. **Time handling** — timezone for schedule evaluation, daily/weekly budget resets, and audit-log timestamps. Plus activity-tracking specifics for the budget feature: multi-device concurrent-use handling, definition of "recent user input" (OS idle vs. process activity vs. network).
6. **Audit-log retention policy** — rolling window (90 days?) once the table starts growing.

## Non-goals (for now)

- macOS, Linux, or non-Windows PC enforcement (will come as agents; not the first agent).
- Cloud-account integration (Microsoft Family Safety, Google Family Link).
- Tamper-resistance against a managed user with OS-level admin privileges on their device (assumes managed users run as standard local users).
- Real-time push (long-polling / SSE) — listed as a feature for completeness but no expected need.
- Multi-tenant / multi-household support.
