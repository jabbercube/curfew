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
| `users` | name (PK), role, managed (bool, default true), target_apps (JSON list), schedule (JSON expr, nullable), budget_minutes (int, nullable) |
| `devices` | name (PK), owner (FK→users, nullable), type, os, mac (JSON list), managed (bool) |
| `apps` | name (PK), exe_paths (JSON list), process_names (JSON list), urls (JSON list) |
| `device_plugins` | (device, type, config JSON) — plugin types expected to govern this device, with per-instance config (e.g. the Windows local user account `windows-pc` should ACL) |
| `core_plugins` | (type, instance_id, governs JSON list, config JSON) — in-core plugins. `governs` is a list of user names this instance covers, or `["*"]` for all governed users (e.g. `adguard` with `governs: ["*"]`) |
| `plugin_instances` | name (PK, e.g. `windows-pc:gamingrig`), type, last_heartbeat, last_seen_version. Derived from `device_plugins` ∪ `core_plugins`: rows are created and deleted in the same transaction as the inventory row. Holds runtime state; inventory holds desired state |
| `plugin_tokens` | token_hash (PK), instance, created_at, revoked_at |
| `user_locks` | user (PK, FK→users), manual_lock (bool), set_at, set_by |
| `audit_log` | id (PK), actor, action, target, payload (JSON), occurred_at |
| `manifests` | type (PK), version, sha256, agent_url — versioned agent artifacts |

### Inventory data model — concepts

**Users** are anyone in the household curfew touches. Two independent axes: `role` (`member` / `manager` / `admin`) determines what a user can do; `managed` (bool) determines whether lock rules apply to them. Schedules and budgets attach to managed users — one user with one PC and one tablet shares one daily budget across both.

| Field | Why it matters |
|-------|----------------|
| `name` | Stable identifier used in CLI/API/UI. Short string (`kid1`), not a real name. |
| `role` | `member`, `manager`, or `admin`. Capabilities are cumulative: `member` has no operator powers; `manager` can lock/unlock managed users; `admin` is everything `manager` is plus can edit users, devices, apps, plugin assignments, and agent manifests. Future auth/RBAC keys off this. |
| `managed` | Bool, defaults to `true`. Whether lock rules apply to this user. Independent of `role` — a teen `manager` could be `managed: true` (has lock control over siblings *and* their own rules apply); a houseguest could be `member, managed: false` (no powers, not subject to rules). When `false`, `GET /v1/users/{user}/status` always returns `{locked: false, reasons: []}` and the rule pipeline is skipped. Operators typically opt out (`--managed false`) for managers, admins, and guests. |
| `target_apps` | Apps that get blocked when this user is in a locked state (manual lock today; out-of-schedule and budget-exhausted in later phases). References keys in `apps`. |
| `schedule` | Optional schedule expression (e.g. `"weekday 16:00-20:00"`). Read by the schedule-lock feature when registered. |
| `budget_minutes` | Optional daily/weekly budget. Read by the budget-lock feature when registered. |

**Devices** are physical things curfew (or a plugin) can act on.

| Field | Why it matters |
|-------|----------------|
| `name` | Used in CLI/UI and as part of plugin instance names (`windows-pc:gamingrig`). |
| `owner` | The user this device belongs to. For managed devices, also the user whose schedule/budget activity here debits. Null = shared device (governed as a group via the shared-device-lock feature when registered). |
| `type` | `pc | laptop | phone | tablet | console | tv`. Constrains which plugin types apply. |
| `os` | `windows | macos | linux | ios | android`. Selects per-device plugin variants. |
| `mac` | Network-layer identity, stable across IP changes. List, since a device commonly has multiple MACs (Wi-Fi + ethernet; randomized per network). Read by network-side plugins. |
| `managed` | Whether curfew governs this device at all. Lets the inventory list devices for completeness without putting them under policy (e.g. a manager's laptop tracked but not enforced). |

**Why people *and* devices, not just users-with-a-list-of-devices:**

- Schedules and budgets attach to **people**, not devices. One member, two devices, one shared budget.
- Plugin coverage is **per-device** — DNS for the phone, OS-level lock for the PC. Different plugins, same person.
- A device may change hands (hand-me-down PC) and its `owner` updates without rewriting policy.

**Out of scope for inventory** (deliberately excluded):

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

## Plugin instance lifecycle

A *plugin instance* is the runtime entity. Instances are declared in inventory:

- **Per-device plugins**: each `device_plugins(device, type)` row produces an instance named `<type>:<device>` (e.g. `windows-pc:gamingrig`).
- **In-core plugins**: each `core_plugins(type, instance_id)` row produces an instance named `<type>` if singular, or `<type>:<instance_id>` if multiple (e.g. `adguard`, or `smart-plug:livingroom`).

Each plugin type registers a **capability descriptor** at core startup, declaring which device `os` and `type` values it applies to, which scopes it supports (`user`, `device`), and which config keys it requires. The descriptor lets the (future) GUI offer constrained dropdowns.

Lifecycle:

1. Operator adds the inventory entry (`curfew plugin assign windows-pc gamingrig`). The core derives the instance name (`windows-pc:gamingrig`) and creates the matching `plugin_instances` row in the same transaction.
2. Operator mints a per-instance bearer token (`curfew plugin token mint windows-pc:gamingrig`). Returns `{id, secret}`; the secret is shown once and never stored in plaintext, the id is what subsequent operations key on (revoke, list).
3. For per-device plugins: operator runs the bootstrap one-liner on the device with the secret. The bootstrap fetches the agent via the versioned manifest (see below).
4. Plugin heartbeats every tick (`POST /v1/plugins/{instance}/heartbeat`).
5. Core compares expected instances (from inventory) to actual heartbeats; surfaces drift on `GET /v1/plugins`.
6. Removal: delete the inventory entry → `plugin_instances` row deleted in the same transaction → core revokes outstanding tokens → next agent tick gets 401 → operator runs uninstall on the device.

## Agent update integrity

Plugins running on devices fetch their agent code from the core. Updates are versioned and hash-verified:

- `GET /v1/agents/{type}/manifest` → `{ version, sha256, url }`.
- Agent compares the manifest's version to its installed version. If different: fetch `url`, verify SHA-256 matches the manifest, swap atomically.
- Update check runs on a slow tick (default hourly); state polling stays on the fast tick (default 60s). Failure to fetch a manifest never blocks state polling.
- Operator publishes a new agent version by uploading an artifact and rotating the `manifests` row.

Future tightening (future feature, not the kernel): manifest signing with a long-lived key embedded in the bootstrap. The kernel design accommodates this without reshape.

## Plugin SDK

The kernel ships a plugin SDK in two flavors:

- **Python SDK** (`curfew_plugin_sdk`) — for in-core plugins and any per-device plugin running on Linux/macOS.
- **PowerShell module** (`curfew-plugin.psm1`) — for windows-pc and any future Windows-resident plugin.

Both expose:

- Polling loop with configurable tick rate
- Heartbeat dispatch with retry/backoff
- Versioned + hash-verified self-update
- Error reporting back to core (via heartbeat payload)
- Config bootstrap (instance name, API URL, token)

A plugin's business logic is the **reconciler** — given the lock status, do the right thing. The SDK handles everything else.

The kernel also ships a **reference test plugin** — a no-op SDK consumer that heartbeats, observes lock-status changes, and writes a sentinel file when locked. The reference plugin lives in the test suite, exercises the SDK end-to-end, and is what the kernel acceptance test runs against. windows-pc is the first non-trivial SDK consumer (a feature on top of the kernel).

## Authentication

- **Plugin auth**: per-instance bearer tokens (ADR-006). Hashed at rest. Read scope is full state in the kernel; future tightening to scoped reads is a feature on top.
- **Operator auth**: V1 uses a single **root bearer token** from env var, distinct from the `admin` user role (which is data-only in V1 — no way for a user with `role: admin` to authenticate yet). Per-user role-based auth (sessions tied to user records) is a feature on top of the kernel; the API surface is shaped to accept it without renames.

## Audit log

Every API write goes through a single audit middleware that records `(actor, action, target, payload, occurred_at)` to the `audit_log` table. The kernel writes the log; reads via API are a feature on top.

## OpenAPI contract

Pydantic v2 schemas everywhere. OpenAPI auto-emitted at `/v1/openapi.json`. CLI types and (future) GUI types generated from the same source — renames become a build error, not a runtime surprise.

## API surface (kernel)

```
Inventory CRUD
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

Plugin lifecycle
  GET    /v1/plugins                            expected instances + heartbeat status (drift)
  GET    /v1/plugins/types                      registered types + capability descriptors
  POST   /v1/plugins/assignments                body { type, target } where target is a device name or "core/{instance_id}"; returns { instance, ... } with derived name
  DELETE /v1/plugins/assignments/{instance}     remove instance from inventory
  POST   /v1/plugins/{instance}/tokens          mint a bearer token; returns { id, secret } — secret shown once
  DELETE /v1/plugins/{instance}/tokens/{id}     revoke
  GET    /v1/plugins/{instance}/tokens          list active token ids (no secrets)
  POST   /v1/plugins/{instance}/heartbeat       liveness (called by the agent)
  POST   /v1/plugins/{instance}/activity        body { user, occurred_at, ... }; no-op until budget rule registered

Locks
  POST   /v1/users/{user}/lock                  manual lock
  POST   /v1/users/{user}/unlock                manual unlock

Effective state
  GET    /v1/users/{user}/status                { locked: bool, reasons: [{kind, ...}] }
  GET    /v1/devices/{device}/status            { locked: bool, reasons: [{kind, ...}] } — empty pipeline in kernel

Agent updates
  GET    /v1/agents/{type}/manifest             { version, sha256, url }
  GET    /v1/agents/{type}/{version}            agent artifact (the agent.ps1 / .py / .sh)
  POST   /v1/agents/{type}/versions             publish a new agent version: body { version, artifact }; rotates the manifest pointer to it

Admin
  GET    /v1/admin/snapshot                     full system snapshot (debug; not the primary read path)
  GET    /v1/health                             liveness probe
```

## CLI shape (kernel)

```
curfew user        add | list | show | edit | rm
curfew device      add | list | show | edit | rm
curfew app         add | list | show | edit | rm
curfew plugin      assign | unassign | types | list | drift
curfew plugin token  mint | revoke | list
curfew agent       publish <type> <version> <file>
curfew lock        <user>
curfew unlock      <user>
curfew status                                # human-readable summary

# Convention: every list/show/status command accepts --json for machine-readable output.
```

## Repository layout

```
curfew/
├── src/
│   ├── curfew/                       # shared library — models, schemas, rule interface, plugin contract
│   ├── curfew_api/                   # FastAPI service
│   │   └── migrations/               # Alembic
│   ├── curfew_cli/                   # CLI — thin HTTP client
│   ├── curfew_plugin_sdk/            # Python plugin SDK
│   └── curfew_plugin_powershell/     # PowerShell plugin SDK module (curfew-plugin.psm1)
├── plugins/
│   └── windows-pc/
│       ├── agent.ps1                 # the per-tick agent (uses the PowerShell SDK)
│       ├── install-agent.ps1         # bootstrap installer
│       └── tests/                    # Pester tests
├── tests/
│   ├── unit/                         # models, schemas, rule pipeline, SDK
│   ├── api/                          # FastAPI TestClient integration tests
│   ├── cli/                          # CLI against a mocked or real API
│   └── e2e/                          # docker-compose-up + CLI roundtrip + reference plugin
├── docker/
│   ├── Dockerfile
│   └── compose.yml
├── docs/
│   ├── PLAN.md
│   └── DECISIONS.md
├── .github/workflows/
│   ├── ci.yml                        # ruff + mypy + pytest (Linux)
│   └── windows.yml                   # Pester (Windows runner)
├── pyproject.toml
└── README.md
```

## Testing strategy

| Layer | Framework | What's covered |
|-------|-----------|----------------|
| Inventory + state models | pytest + in-memory SQLite | CRUD round-trips, schema validation, migrations apply forward, FK/uniqueness behaviour. ≥90% coverage. |
| Rule pipeline | pytest | Rule registration, OR composition, reasons aggregation, behaviour with zero rules. |
| Plugin contract | pytest with a fake plugin | Reference test plugin exercises the full contract; serves as living documentation. |
| Plugin SDK (Python) | pytest | Polling loop, heartbeat retry, manifest fetch, hash mismatch refusal. |
| Plugin SDK (PowerShell) | Pester | Same coverage, on the Windows runner. |
| API | pytest + FastAPI TestClient | Every endpoint: happy path, auth failures, validation errors, idempotency. |
| Agent update flow | pytest + Pester | Manifest endpoint, hash verification, atomic swap, rollback on failed swap. |
| CLI | pytest + httpx mocking | Each command, exit codes, output formatting. |
| Audit log | pytest | Every API write produces a row; payloads are structured. |
| End-to-end | pytest + docker compose | Spin up the stack, run CLI, observe plugin instance heartbeat + drift. |

CI runs on every push:
- **Linux runner**: ruff + mypy + pytest (unit, API, CLI, e2e with docker compose).
- **Windows runner**: Pester for the PowerShell SDK + windows-pc agent + installer smoke test.

Pre-commit hooks for lint/format. Type hints required (`mypy --strict` for the core).

## Acceptance: kernel done

The kernel is "done" when this end-to-end walkthrough passes against the **reference test plugin** (a no-op SDK consumer in the test suite that heartbeats, observes lock-status changes, and writes a sentinel file when locked). The reference plugin exercises the kernel end-to-end without depending on any feature-level plugin.

1. CLI creates a user (`curfew user add kid1 --role member --managed true`), a device (`curfew device add gamingrig --owner kid1 --type pc --os windows`), and an app (`curfew app add steam --exe-path ...`).
2. CLI publishes the reference plugin's first version (`curfew agent publish reftest 1.0.0 ./reftest.py`).
3. CLI assigns the reference plugin to the device (`curfew plugin assign reftest gamingrig`); verify the `plugin_instances` row was created in the same transaction.
4. CLI mints a token (`curfew plugin token mint reftest:gamingrig`); CLI prints the secret once and the bootstrap one-liner.
5. The reference plugin runs the bootstrap. The bootstrap fetches the manifest, verifies SHA-256, installs the plugin.
6. Plugin heartbeats; `GET /v1/plugins` shows it healthy.
7. Stop the plugin; `GET /v1/plugins` shows drift within 2 ticks.
8. CLI locks the user (`curfew lock kid1`); the manual_lock rule fires; `GET /v1/users/kid1/status` returns `{locked: true, reasons: [{kind: "manual_lock"}]}`.
9. The reference plugin reads the lock status and writes its sentinel file.
10. CLI unlocks; the reference plugin clears its sentinel.
11. CLI removes the assignment (`curfew plugin unassign reftest:gamingrig`); the `plugin_instances` row is deleted, outstanding tokens are revoked, the next heartbeat gets 401.
12. `audit_log` contains a structured row for every API write in the sequence.

windows-pc lands as the first real feature immediately after the kernel passes — same lifecycle, but with NTFS ACLs and process kill replacing the sentinel file.

---

# Features

Features stack on top of the kernel without reshaping it. **Order is arbitrary** — implement whichever is most needed when. The list below is roughly ordered by logical dependency (manual lock first because it's the kernel's reference rule; budget after schedule because both build on the same rule machinery), but the order isn't load-bearing.

## Manual lock rule (kernel reference)

The first rule registered (user scope). Reads `user_locks.manual_lock`; returns `{kind: "manual_lock"}` when set. Ships with the kernel as the proof-of-concept rule.

## Schedule lock rule

A new user-scope rule. Reads `users.schedule`; evaluates against current time + timezone; returns `{kind: "out_of_schedule", schedule: "..."}` when out of window. CLI: `curfew schedule <user> "<expr>"`. Tests: expression parsing, time-zone handling, transitions across midnight/DST.

## Budget lock rule + activity ingestion

Adds `usage_minutes(user, day, minutes)` table via Alembic migration. Plugins call `POST /v1/plugins/{instance}/activity` with `{user, occurred_at}` when they observe recent user input — the kernel endpoint that was a no-op now writes to `usage_minutes`. A new user-scope rule returns `{kind: "budget_exhausted", consumed: 120, budget: 90}` when `users.budget_minutes` is consumed for the period. Resets daily/weekly. CLI: `curfew budget <user> <minutes>`.

## Shared-device lock + `--shared` operations

A new **device-scope rule** — the first to register into the device-scope pipeline. Adds a device-level `shared_lock` flag (or row). The rule reads it for any device with `owner = NULL` and returns `{kind: "shared_lock"}` when set. CLI: `curfew lock --shared` and `curfew unlock --shared`. Endpoints: `POST /v1/shared/lock` / `unlock`. Plugins on shared devices read `GET /v1/devices/{device}/status`.

## Per-device app overrides

Adds a `device_app_overrides(device, app, exe_paths, process_names, urls)` table. Resolution path in the core: when a plugin reads the lock status for a device, the global app entry is overridden by the device's row if present. **Replace** semantics — override list replaces global list, not merge.

## Plugins

Each plugin is a new type with: a capability descriptor, an SDK consumer (Python or PowerShell), and a bootstrap one-liner if per-device. Implementations:

- **`windows-pc`** (per-device, PowerShell SDK) — first plugin; stress-tests the SDK and shapes the contract. NTFS deny-execute on configured exe paths + Chrome/Edge `URLBlocklist` registry policy. Kills matching running processes.
- **`adguard`** (in-core, Python SDK) — first in-core plugin; stress-tests the in-core path. DNS sinkhole via AdGuard Home REST API.
- **`smart-plug`** (in-core, Python SDK) — Tasmota/Kasa power control.
- **`router-acl`** (in-core, Python SDK) — UniFi/OPNsense API.
- **`tailscale-acl`** (in-core, Python SDK) — gate egress for managed devices on the tailnet.
- **`macos-pc`** (per-device, Python SDK on macOS) — same primitives translated.

## GUI

Web UI bundled into the same docker image. Reads `/v1/openapi.json` for types. Operates on inventory through the same CRUD endpoints the CLI uses. No new architectural commitments.

## Agent manifest signing

Sign the manifest with a long-lived private key on the core. Embed the public key in the bootstrap. Agent verifies the signature before checking the hash. Drop-in over the existing manifest endpoint; closes the trust gap if the core itself is partially compromised.

## YAML import side tool

Optional. A CLI command (`curfew import yaml inventory.yaml`) or external script that reads YAML and POSTs through inventory CRUD. The architecture doesn't depend on it; this is a UX preference.

## Push (long-polling / SSE)

Almost certainly never needed. Pull-based at 60s tick is fine for screentime forever. Listed here for completeness; defer until there's a concrete use case that pull can't satisfy.

---

## Open questions

Implementation-level decisions still pending — none are kernel-architectural:

1. **Plugin agent language for `windows-pc`** — pure PowerShell (zero deps on Windows) or bundled Python (cleaner state logic, requires runtime install)? *Lean: PowerShell for kid PCs to stay dependency-free.*
2. **Mid-session lock behaviour** — kill running processes immediately, force logoff, or just block future launches? *Lean: kill matching processes (with a config knob to opt out).*
3. **Initial app list** — Steam, Minecraft, YouTube definite; decide on Roblox, Discord, Twitch.
4. **CI runners** — public GitHub Actions (free Windows runner) vs. self-hosted on the homelab? *Lean: GitHub Actions until private code or speed becomes an issue.*
5. **Activity-tracking specifics** (for the budget feature, not the kernel) — timezone for daily/weekly resets, multi-device concurrent-use handling, definition of "recent user input" (OS idle vs. process activity vs. network).
6. **Audit-log retention policy** — rolling window (90 days?) once the table starts growing.

## Non-goals (for now)

- macOS, Linux, or non-Windows PC enforcement (will come as plugins; not the first plugin).
- Cloud-account integration (Microsoft Family Safety, Google Family Link).
- Tamper-resistance against a managed user with OS-level admin privileges on their device (assumes managed users run as standard local users).
- Real-time push (long-polling / SSE) — listed as a feature for completeness but no expected need.
- Multi-tenant / multi-household support.
