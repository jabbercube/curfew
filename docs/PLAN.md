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
|     kid1         |     |  state.json (volume)  |     |  Scheduled Task, every 1min |
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

- **Parent's machine** — CLI binary/script. Stateless. Each invocation is short-lived. Talks to the core's API over HTTPS.
- **Homelab host** — one docker compose stack: a single FastAPI container serving the API, fronted by your existing Traefik. State persists to a JSON or SQLite file in a docker volume.
- **Each kid PC** — small PowerShell agent + a Windows Scheduled Task. Installed once via a bootstrap script. The agent fetches state from the API on each tick and reconciles local enforcement.

## Plugin model

A plugin is anything that:

1. **Authenticates** to the core API with a per-plugin bearer token.
2. **Reads state** via `GET /v1/state` (or a filtered variant for plugins).
3. **Reconciles** its slice of the world to match. Idempotent — if state hasn't changed, no-op.
4. **Identifies itself** (name + targets it governs) so the API can show "kid1 is governed by `windows-pc:gamingrig` and `adguard`."
5. **Optionally heartbeats** so the API knows the plugin is alive (`POST /v1/plugins/{name}/heartbeat`).

That's the whole contract. Concretely a plugin is a long-running poller (or a cron-driven script) plus a small config file telling it where the API lives and which targets it cares about.

### Plugin types: in-core vs. per-device

Plugins fall into two categories based on where they run:

**In-core plugins** — run inside (or as a sidecar to) the curfew-core docker stack. Reach out to external systems from there.
- Example: `adguard` plugin (talks to AdGuard's REST API), `smart-plug` (Tasmota HTTP), `router-acl` (UniFi/OPNsense API).
- Distribution: ship as part of the core docker image, or as additional compose services.

**Per-device plugins** — run *on* the device they govern, because they need OS-level access there.
- Example: `windows-pc` (NTFS ACLs, registry policy), eventually `macos-pc`.
- Distribution: a one-time install script per device. The agent then self-updates by fetching the latest version from the core on each tick.

Both kinds talk to the same API the same way. The difference is purely deployment.

## State shape (target)

```json
{
  "users": {
    "kid1": {
      "manual_lock": true,
      "blocks": ["steam", "minecraft", "youtube"]
    }
  },
  "apps": {
    "steam":     { "exe_paths": ["C:\\Program Files (x86)\\Steam\\steam.exe"],
                   "process_names": ["steam"] },
    "minecraft": { "exe_paths": ["C:\\Users\\*\\AppData\\Roaming\\.minecraft\\..."],
                   "process_names": ["javaw"] },
    "youtube":   { "urls": ["youtube.com", "*.youtube.com", "youtu.be"] }
  },
  "plugins": {
    "windows-pc:gamingrig": { "user": "kid1", "last_seen": "2026-05-05T18:00:00Z" },
    "adguard":              { "users": ["kid1", "kid2"], "last_seen": "..." }
  }
}
```

State is intentionally plugin-agnostic — `apps` describes apps in plugin-neutral terms; each plugin reads what's relevant to it (a Windows plugin uses `exe_paths`; a hypothetical macOS plugin uses bundle IDs from the same entry).

## API surface (V1 target)

```
POST   /v1/auth/token                     # exchange long-lived secret for short token (later)
GET    /v1/state                          # full state (admin/debug)
GET    /v1/state/effective/{user}         # computed effective lock + reasons (for plugins)
POST   /v1/users/{user}/lock              # manual lock
POST   /v1/users/{user}/unlock            # manual unlock
GET    /v1/users                          # list users + summary
GET    /v1/plugins                        # registered plugins + last seen
POST   /v1/plugins/{name}/heartbeat       # plugin liveness
```

V2 adds `/v1/users/{user}/schedule`. V3 adds `/v1/users/{user}/budget` + `POST /v1/heartbeat` (activity tracking).

## Repository layout

```
curfew/
├── src/
│   ├── curfew/                     # shared library (state model, plugin contract, schemas)
│   ├── curfew_api/                 # FastAPI service — the docker-hosted core
│   └── curfew_cli/                 # CLI — thin HTTP client
├── plugins/
│   └── windows-pc/
│       ├── agent.ps1               # the per-tick agent
│       ├── install-agent.ps1       # bootstrap installer
│       └── tests/                  # Pester tests
├── tests/
│   ├── unit/                       # state model, schema validation
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
| State model | pytest | All transitions, schema validation, persistence round-trips. ≥90% coverage. |
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

- **Core**: FastAPI service in a single docker container. JSON-file state. Bearer auth. Endpoints: `GET /v1/state`, `GET /v1/state/effective/{user}`, `POST /v1/users/{user}/lock`, `POST /v1/users/{user}/unlock`, `POST /v1/plugins/{name}/heartbeat`.
- **CLI**: `curfew lock <user>`, `curfew unlock <user>`, `curfew status`. Reads config from `~/.config/curfew/config` (API URL + bearer).
- **Plugin: `windows-pc`**: PowerShell agent + Scheduled Task. Polls every minute. Applies/removes NTFS deny-execute on configured .exe paths + Chrome/Edge `URLBlocklist` registry policy. Kills matching running processes. Self-updates from `/plugins/windows-pc/agent.ps1` on the core.
- **Bootstrap**: one-line PowerShell installer registers the scheduled task, drops `agent.config` (API URL, bearer, governed Windows user).
- **Acceptance**: from your laptop, `curfew lock kid1` → within 60 seconds, Steam can't launch on the kid PC and YouTube is blocked in Chrome/Edge. `curfew unlock kid1` reverses it.
- **Tests**: full pytest suite green; Pester tests green; e2e test (`docker compose up` + CLI round-trip) green in CI.

### V2 — schedules (additive)

- State adds `schedule` per user: `"weekday 16:00-20:00"`.
- **Schedule evaluation lives in core** — plugins still see only `effective_lock: bool`.
- CLI grows: `curfew schedule <user> "<expr>"`.
- New tests: schedule expression parsing, time-zone handling, transitions across midnight/DST.

### V3 — budgets (additive)

- Plugins gain a heartbeat for input-activity: `POST /v1/heartbeat` when there's recent user input.
- Core tallies usage per user, decrements `budget_minutes_remaining` (resets daily/weekly).
- Effective lock = `manual_lock || out_of_schedule || budget_exhausted`.
- New tests: budget accounting, heartbeat dedup, activity-window logic.

### V4 — web UI + second plugin

- Web UI (Vite + a small SPA, or HTMX-style server-rendered) bundled into the same docker image. Same API.
- Build an `adguard` in-core plugin (DNS sinkhole via AdGuard Home's REST API).
- Tests: API contract tests pinned (regression guard once UI exists); plugin tests for `adguard`.

### V5+ — backlog plugin candidates

- `smart-plug` — Tasmota/Kasa power control.
- `router-acl` — UniFi/OPNsense API.
- `tailscale-acl` — gate egress for tailnet kid devices.
- `macos-pc` — same primitives translated.
- Long-polling / SSE push so plugins react in seconds rather than within-the-tick latency.

## Open questions (decide before V1)

1. **State backend** — single JSON file (simplest) or SQLite (better concurrent writes, easier query for V3 budget tally)? Lean: start JSON, migrate to SQLite when V3 lands.
2. **Auth model** — single shared bearer for V1 vs. per-plugin tokens from day one? Lean: per-plugin tokens from day one — small upfront cost, big audit/security win later.
3. **API serialization** — Pydantic v2 schemas everywhere; emit OpenAPI for docs and future TS client gen.
4. **Plugin agent language for `windows-pc`** — pure PowerShell (zero deps on Windows) or Python (cleaner JSON / state logic, requires runtime install)? Lean: PowerShell to keep kid PCs dependency-free.
5. **Mid-session lock behaviour** — kill running processes immediately, force logoff, or just block future launches? Lean: kill matching processes (with a config knob to opt out).
6. **Initial app list** — Steam, Minecraft, YouTube definite; decide now on Roblox, Discord, Twitch.
7. **CI runners** — public GitHub Actions (free Windows runner) vs. self-hosted on the homelab? Lean: GitHub Actions until private code or speed becomes an issue.

## Initial milestones

1. Decide the open questions (or accept the leans).
2. Stand up project skeleton: pyproject, ruff/mypy/pytest config, CI workflows. Verify "hello world" tests pass on Linux + Windows runners.
3. Define and write tests for the state model + plugin contract first (TDD).
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
