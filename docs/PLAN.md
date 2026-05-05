# curfew — Plan

CLI- (and eventually web-) driven tool to manage kid screentime across the household. Built around an **API-first, plugin-based** architecture: a small dockerized core service holds state and exposes an HTTP API; *plugins* implement specific enforcement methods (Windows PC lockout, DNS sinkhole, smart plugs, router ACLs, etc.). New methods are added by writing new plugins. The CLI is a thin client of the API; a web UI lands later as another client.

## Goals

1. **Modular** — each enforcement method is an independent plugin. The Windows PC enforcer is just one of many.
2. **API-first** — the core HTTP API is the contract. CLI, GUI, plugins, and any future surface are all clients of it.
3. **Docker-hosted core** — assume the user runs the core as a docker compose stack alongside their other homelab services. Matches the `docker/apps/` pattern.
4. **CLI today, GUI later** — same API serves both. No refactor needed when the GUI lands.
5. **Pull-based enforcement** — plugins reconcile against state on a timer. Self-heals through reboots, sleep, and network changes. Push (long-polling) is a future addition.
6. **Well-tested** — the core, the CLI, and the plugin agents all have automated tests running in CI.

## Architecture & topology

```
+------------------+     +-----------------------+     +-----------------------------+
| Parent's machine |     | Homelab host          |     | Each kid PC (Windows)       |
| (your laptop)    |     | (docker compose)      |     |                             |
|                  |     |                       |     |                             |
|  $ curfew lock   | --> |  curfew-core (FastAPI)| <-- |  agent.ps1                  |
|     kid1         |     |  inventory.yaml +     |     |  Scheduled Task, every 1min |
|                  |     |  state.sqlite (vols)  |     |  applies icacls + registry  |
|                  |     |  behind Traefik       |     |                             |
|  curfew CLI      |     |                       |     |                             |
|  (HTTP client)   |     |  + future: web UI     |     |  installed once via         |
|                  |     |    served by same     |     |  install-agent.ps1          |
|                  |     |    container          |     |                             |
+------------------+     +-----------------------+     +-----------------------------+

   talks to API           the only "always-on"          per-device install — bootstrap
   over HTTPS             component. one container.    once, self-updates after.
```

Three places code physically lives:

- **Parent's machine** — CLI binary/script. Stateless. Each invocation is short-lived. Talks to the core's API over HTTPS.
- **Homelab host** — one docker compose stack: a single FastAPI container serving the API, fronted by your existing Traefik. Persists `inventory.yaml` (config volume, human-edited) and `state.sqlite` (data volume, API-written) — see the Storage model section.
- **Each kid PC** — small PowerShell agent + a Windows Scheduled Task. Installed once via a bootstrap script. The agent fetches state from the API on each tick and reconciles local enforcement.

## Plugin model

A plugin is anything that:

1. **Authenticates** to the core API with a per-plugin bearer token.
2. **Reads state** via `GET /v1/state/effective/{user}` (or a filtered variant).
3. **Reconciles** its slice of the world to match. Idempotent — if state hasn't changed, no-op.
4. **Heartbeats** on every tick (`POST /v1/plugins/{name}/heartbeat`) so the core can detect drift between expected instances (from `inventory.yaml`) and actual liveness (per ADR-008).

That's the whole contract. Concretely a plugin is a long-running poller (or a cron-driven script) plus a small config file telling it where the API lives and which instance name (e.g. `windows-pc:gamingrig`) it identifies as. The set of expected instances is declared in `inventory.yaml` — plugins do not announce themselves.

### Plugin types: in-core vs. per-device

Plugins fall into two categories based on where they run:

**In-core plugins** — run inside (or as a sidecar to) the curfew-core docker stack. Reach out to external systems from there.
- Example: `adguard` plugin (talks to AdGuard's REST API), `smart-plug` (Tasmota HTTP), `router-acl` (UniFi/OPNsense API).
- Distribution: ship as part of the core docker image, or as additional compose services.

**Per-device plugins** — run *on* the device they govern, because they need OS-level access there.
- Example: `windows-pc` (NTFS ACLs, registry policy), eventually `macos-pc`.
- Distribution: a one-time install script per device. The agent then self-updates by fetching the latest version from the core on each tick.

Both kinds talk to the same API the same way. The difference is purely deployment.

## Storage model

Persistent data lives in two places, one human-edited and one machine-written:

- **`inventory.yaml`** — users, devices, and the global app catalog. Edited by hand (or by a CLI command that rewrites the file). Loaded on startup; reloaded on demand.
- **`state.sqlite`** — runtime state written by the API: user locks, plugin heartbeats and last-seen, future budget tallies and audit log. SQLite from V1 with SQLModel + Alembic migrations.

See [DECISIONS.md](DECISIONS.md) ADR-005 (SQLite from V1) and ADR-007 (inventory file split) for rationale.

### `inventory.yaml` shape (V1)

```yaml
users:
  kid1:
    role: child
    blocks: [steam, minecraft, youtube]
  parent:
    role: parent

devices:
  gamingrig:
    owner: kid1            # user that activity here debits; null/omitted = shared
    type: pc               # pc | laptop | phone | tablet | console | tv
    os: windows            # windows | macos | linux | ios | android
    managed: true
    mac: ["aa:bb:cc:dd:ee:ff"]
    plugins: [windows-pc]  # plugin types expected to govern this device

apps:
  steam:
    exe_paths: ['C:\Program Files (x86)\Steam\steam.exe']
    process_names: [steam]
  minecraft:
    exe_paths: ['C:\Users\*\AppData\Roaming\.minecraft\...']
    process_names: [javaw]
  youtube:
    urls: [youtube.com, "*.youtube.com", youtu.be]
```

The inventory is intentionally plugin-agnostic — `apps` describes apps in plugin-neutral terms; each plugin reads what's relevant to it (a Windows plugin uses `exe_paths`; a hypothetical macOS plugin uses bundle IDs from the same entry).

**Per-field notes:**

*Users:*
- `name` (the YAML key) — stable identifier used in CLI, API, and UI. Short string (`kid1`), not a real name.
- `role` — `parent` or `child`. Distinguishes who governs from who is governed; future auth/RBAC keys off this.
- `blocks` — apps to block when this user is locked. References keys in the `apps` catalog.

*Devices:*
- `name` (the YAML key) — used in CLI/UI and as part of plugin instance names (`windows-pc:gamingrig`).
- `owner` — the user whose schedule/budget activity here debits. Omit (or set null) for shared devices (common-area TV, family tablet); the `--shared` flag governs those as a group from V2 onward.
- `type` — `pc | laptop | phone | tablet | console | tv`. Informs which plugins are applicable; a phone never gets `windows-pc`.
- `os` — `windows | macos | linux | ios | android`. Selects per-device plugin variants when one exists.
- `mac` — list. Network-layer identity, stable across IP changes; needed by network-side plugins (DNS sinkhole, router ACL). A device commonly has multiple MACs (laptop with Wi-Fi *and* ethernet; phones/tablets that randomize per network) — all entries identify the same device.
- `managed` — whether curfew governs this device. Lets the inventory list parents' devices for completeness without putting them under policy.
- `plugins` — plugin types expected to govern this device. Inventory-as-desired-state per ADR-008.

**Why people *and* devices, not just users-with-a-list-of-devices:**

- Schedules and budgets attach to **people**, not devices. One kid, two devices, one shared budget.
- Plugin coverage is **per-device** — DNS for the phone, OS-level lock for the PC. Different plugins, same person.
- A device may change hands (hand-me-down PC) and its `owner` updates without rewriting policy.

**Out of scope for inventory** (deliberately excluded):

- VLAN / network segmentation — a network concern, not a screentime one.
- Static-vs-dynamic IP — irrelevant; we key on MAC.
- Hardware specs, purchase dates, warranty — asset-management territory.

### `state.sqlite` schema (V1)

| Table | Purpose |
|-------|---------|
| `user_locks` | One row per user. `manual_lock: bool`, `set_at`, `set_by`. |
| `plugin_instances` | One row per expected instance (derived from inventory on startup/reload). `name` (e.g. `windows-pc:gamingrig`), `last_heartbeat`, `last_seen_version`. |
| `plugin_tokens` | Per-plugin bearer tokens (per ADR-006). Hashed at rest. |

V2 adds `schedules`. V3 adds `usage_minutes` (budget tally) and `audit_log`. Migrations are tracked from day one so V2/V3 are additive, not panicked ports.

### Plugin instance naming

Plugin instances are referenced as `<type>` for in-core plugins (`adguard`, `smart-plug`) and `<type>:<device-name>` for per-device plugins (`windows-pc:gamingrig`, `macos-pc:laptop`). The expected set of instances is derived from `inventory.yaml`: each `devices.{name}.plugins` entry that references a per-device type produces one instance; in-core plugin types produce one instance per configured deployment.

## API surface (V1 target)

```
POST   /v1/auth/token                     # exchange long-lived secret for short token (later)
GET    /v1/state                          # merged inventory + runtime view (admin/debug)
GET    /v1/state/effective/{user}         # computed effective lock + reasons (for plugins)
POST   /v1/users/{user}/lock              # manual lock
POST   /v1/users/{user}/unlock            # manual unlock
GET    /v1/users                          # list users + summary
GET    /v1/plugins                        # expected instances (from inventory) + heartbeat status (drift visible here)
POST   /v1/plugins/{name}/heartbeat       # plugin liveness
POST   /v1/admin/reload-inventory         # re-read inventory.yaml from disk
```

V2 adds `/v1/users/{user}/schedule`, device-as-resource endpoints, and shared-device locking (`POST /v1/shared/lock`/`unlock`). V3 adds `/v1/users/{user}/budget` + `POST /v1/heartbeat` (activity tracking). V5+ adds per-device app overrides.

## Repository layout

```
curfew/
├── src/
│   ├── curfew/                     # shared library (inventory + state models, plugin contract, schemas)
│   ├── curfew_api/                 # FastAPI service — the docker-hosted core
│   │   └── migrations/             # Alembic migrations for state.sqlite
│   └── curfew_cli/                 # CLI — thin HTTP client
├── plugins/
│   └── windows-pc/
│       ├── agent.ps1               # the per-tick agent
│       ├── install-agent.ps1       # bootstrap installer
│       └── tests/                  # Pester tests
├── tests/
│   ├── unit/                       # inventory + state models, schema validation
│   ├── api/                        # FastAPI TestClient integration tests
│   ├── cli/                        # CLI against a mocked or real API
│   └── e2e/                        # docker-compose-up + CLI roundtrip
├── docker/
│   ├── Dockerfile                  # builds curfew_api into a slim image
│   └── compose.yml                 # for homelab deployment
├── docs/
│   ├── api.md                      # API contract (or auto-gen from OpenAPI)
│   ├── plugin-contract.md          # how to write a plugin
│   └── topology.md                 # what runs where, how to deploy
├── examples/
│   └── inventory.example.yaml      # commented inventory template
├── .github/workflows/
│   ├── ci.yml                      # lint, type-check, pytest (Linux)
│   └── windows.yml                 # Pester tests on Windows runner
├── pyproject.toml
└── PLAN.md
```

## Testing strategy

Testing is first-class. Rough targets:

| Layer | Framework | What's covered |
|-------|-----------|----------------|
| Inventory model | pytest | YAML load/validate, schema errors surfaced clearly, reload semantics. |
| State model | pytest + in-memory SQLite | All transitions, schema validation, migrations apply cleanly forward, persistence round-trips. ≥90% coverage. |
| API | pytest + FastAPI TestClient (httpx) | Every endpoint: happy path, auth failures, validation errors, idempotency. |
| Plugin contract | pytest with a fake plugin | A reference test plugin exercises the full contract; serves as living documentation. |
| CLI | pytest + httpx mocking | Each command, exit codes, output formatting. |
| Windows agent | Pester (Windows CI runner) | ACL apply/revert, registry policy, idempotency, error paths. |
| End-to-end | pytest + docker compose | Spin up the stack, run CLI commands, assert state changes propagate. |

CI runs on every push:
- **Linux runner**: ruff + mypy + pytest (unit, API, CLI, e2e with docker compose).
- **Windows runner**: Pester for the `windows-pc` plugin agent + installer smoke test.

Pre-commit hooks for lint/format. Type hints required (`mypy --strict` for the core).

## Phasing

### V1 — core API + CLI + `windows-pc` plugin, on-demand

**Goal:** prove the full loop with one real plugin and a working CLI. No schedules, no UI.

- **Core**: FastAPI service in a single docker container. `inventory.yaml` (mounted from a config volume) + `state.sqlite` (in a data volume). SQLModel + Alembic from day one. Per-plugin bearer auth (ADR-006). Endpoints: `GET /v1/state`, `GET /v1/state/effective/{user}`, `POST /v1/users/{user}/lock`, `POST /v1/users/{user}/unlock`, `GET /v1/plugins` (with drift status), `POST /v1/plugins/{name}/heartbeat`, `POST /v1/admin/reload-inventory`.
- **CLI**: `curfew lock <user>`, `curfew unlock <user>`, `curfew status`. Reads config from `~/.config/curfew/config` (API URL + bearer).
- **Plugin: `windows-pc`**: PowerShell agent + Scheduled Task. Polls every minute. Applies/removes NTFS deny-execute on configured .exe paths + Chrome/Edge `URLBlocklist` registry policy. Kills matching running processes. Self-updates from `/plugins/windows-pc/agent.ps1` on the core.
- **Bootstrap**: one-line PowerShell installer registers the scheduled task, drops `agent.config` (API URL, bearer, instance name e.g. `windows-pc:gamingrig`).
- **Acceptance**: from your laptop, `curfew lock kid1` → within 60 seconds, Steam can't launch on the kid PC and YouTube is blocked in Chrome/Edge. `curfew unlock kid1` reverses it.
- **Tests**: full pytest suite green; Pester tests green; e2e test (`docker compose up` + CLI round-trip) green in CI.

### V2 — schedules + device endpoints + shared-device locking (additive)

- State adds `schedule` per user: `"weekday 16:00-20:00"`. **Schedule evaluation lives in core** — plugins still see only `effective_lock: bool`.
- Device-as-resource endpoints: `GET /v1/devices`, `GET /v1/devices/{name}`, optional per-device filtered state for plugins.
- Shared-device locking: state gains `shared_lock: bool`, API gains `POST /v1/shared/lock`/`unlock`, CLI gains `curfew lock --shared`. Locks are unconditional (a shared lock blocks everyone).
- CLI grows: `curfew schedule <user> "<expr>"`, `curfew lock --shared`.
- New tests: schedule expression parsing, time-zone handling, transitions across midnight/DST, shared-lock propagation.

### V3 — budgets (additive)

- Plugins gain a heartbeat for input-activity: `POST /v1/heartbeat` when there's recent user input.
- Core tallies usage per user, decrements `budget_minutes_remaining` (resets daily/weekly).
- Effective lock = `manual_lock || out_of_schedule || budget_exhausted`.
- New tests: budget accounting, heartbeat dedup, activity-window logic.

### V4 — web UI + second plugin

- Web UI (Vite + a small SPA, or HTMX-style server-rendered) bundled into the same docker image. Same API.
- Build an `adguard` in-core plugin (DNS sinkhole via AdGuard Home's REST API).
- Tests: API contract tests pinned (regression guard once UI exists); plugin tests for `adguard`.

### V5+ — backlog plugin candidates and deferred features

Plugin candidates:

- `smart-plug` — Tasmota/Kasa power control.
- `router-acl` — UniFi/OPNsense API.
- `tailscale-acl` — gate egress for tailnet kid devices.
- `macos-pc` — same primitives translated.

Deferred features (no plugin work):

- **Per-device app overrides** — let a single device declare alternate paths/process names for an app in the global catalog. Replace semantics (override list replaces global list for that device, not merge). Deferred until enough installs exist that non-default paths are common in practice.
- **Long-polling / SSE push** so plugins react in seconds rather than within-the-tick latency.
- **Per-instance scoped tokens** — V1 uses per-plugin-type tokens (ADR-006); per-instance scoping (a `windows-pc:gamingrig` token can only see kid1's state) is a future tightening.

## Open questions (decide before V1)

Resolved during the reconciliation pass — see DECISIONS.md for details:

- ~~State backend~~ → SQLite from V1, with `inventory.yaml` separate (ADR-005, ADR-007).
- ~~Auth model~~ → per-plugin bearer tokens from day one (ADR-006).
- ~~Plugin assignment direction~~ → inventory-as-desired-state + heartbeats (ADR-008).
- ~~User vs people terminology~~ → `users` with `role` field.

Still open:

1. **API serialization** — Pydantic v2 schemas everywhere; emit OpenAPI for docs and future TS client gen. *(Lean: yes, no real alternative.)*
2. **Plugin agent language for `windows-pc`** — pure PowerShell (zero deps on Windows) or Python (cleaner JSON / state logic, requires runtime install)? *Lean: PowerShell to keep kid PCs dependency-free.*
3. **Mid-session lock behaviour** — kill running processes immediately, force logoff, or just block future launches? *Lean: kill matching processes (with a config knob to opt out).*
4. **Initial app list** — Steam, Minecraft, YouTube definite; decide now on Roblox, Discord, Twitch.
5. **CI runners** — public GitHub Actions (free Windows runner) vs. self-hosted on the homelab? *Lean: GitHub Actions until private code or speed becomes an issue.*

## Areas to revisit

- **Plugin architecture** (ADR-008) — types vs instances, capability registry, scoped tokens, drift. The end-state model is sketched in the ADR but the V1 cut is intentionally minimal. Revisit before starting V2 to reconfirm direction.

## Initial milestones

1. Resolve the remaining open questions above (or accept the leans).
2. Stand up project skeleton: pyproject, ruff/mypy/pytest config, CI workflows, SQLModel + Alembic with one empty initial migration, `examples/inventory.example.yaml`. Verify "hello world" tests pass on Linux + Windows runners.
3. Define and write tests for the inventory model (YAML load/validate), state schema (SQLModel + Alembic round-trips), and plugin contract first (TDD).
4. Implement core API (V1 endpoints) to satisfy the tests.
5. Build CLI against the API.
6. Build `windows-pc` agent + installer; Pester tests; manual install on one test PC.
7. End-to-end test: `docker compose up` + CLI commands + agent on a real PC.
8. Live with V1 a few weeks before starting V2.

## Non-goals (for now)

- macOS, Linux, or non-Windows PC enforcement (will come as plugins; not V1).
- Cloud-account integration (Microsoft Family Safety, Google Family Link).
- Tamper-resistance against an admin-level kid (assumes kids run as standard Windows users).
- Real-time push (long-polling/SSE). Pull-only is sufficient for V1–V3.
- Multi-tenant / multi-household support.
