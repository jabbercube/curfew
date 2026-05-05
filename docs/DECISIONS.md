# Architecture decisions

Short notes on the major design decisions and rejected alternatives. Captures the *why* so we don't relitigate later.

## ADR-001: API-first hosted core

**Decided:** run a small FastAPI service in a docker container as the system of record. CLI is a thin HTTP client; future GUI is another HTTP client; agents (per-device, polling) are HTTP clients too. Plugins (in-core Python modules) live inside this same process and are not HTTP clients.

**Rejected:** pure-library design (CLI imports core functions directly, writes to a file on disk).

**Why:**

- A web GUI accessible from a phone is a stated long-term goal. Phone access requires a server.
- Multiple concurrent writers (CLI + GUI + agents heartbeating) need a daemon to serialize. Library + file on disk means rolling our own locking — easy to get wrong.
- Future features (heartbeats, budgets, audit log, push) all need persistent compute beyond a static file.
- Docker is already the homelab pattern; one more compose stack is genuinely cheap operational cost.

Library-first remains the right call only if the project ever scopes back to terminal-only with no remote access. Not the plan.

## ADR-002: SQLite is the only persistent store

**Decided:** all persistent data — users, devices, apps, agent installs, plugin assignments, runtime state, tokens, audit log, agent manifests — lives in a single `state.sqlite` file. SQLModel for the schema, Alembic for migrations from day one.

**Rejected:**

- **Multi-store split (e.g. YAML data + SQLite runtime).** Felt natural early on — human-edited records (users, devices, apps) and machine-written runtime have different lifecycles. Breaks down the moment a CLI or GUI tries to write: YAML round-tripping that preserves comments and ordering is fragile, and bidirectional sync between YAML and a runtime store is famously hard. Cleaner to put everything in SQLite and offer YAML as an *optional client-side import tool* if anyone wants it (just another HTTP client of the user/device/app CRUD endpoints).
- **Postgres.** Overkill for a homelab single-tenant store. Adds an operational dependency.
- **JSON file.** Concurrency-unsafe with multiple writers. Schema-less, so every change is a custom migration loader.

**Why SQLite is enough:**

- WAL mode handles concurrent readers + a writer cleanly.
- Backups are `cp` of one file (or `.backup` for atomic).
- Inspection is `sqlite3 state.sqlite '.dump'` or any GUI.
- SQLModel gives Pydantic-compatible models with `create_all()`; Alembic versions migrations from day one so feature additions are additive, not panicked ports.

## ADR-003: Pull-based agent reconciliation; event-driven plugins

**Decided:** the two extension surfaces have different trigger mechanisms because of where they run:

- **Agents** (on-device) poll the API on a timer (default 60s) and reconcile their slice of state. The core can't reach them — kid PC behind NAT, sleeping, intermittently online — so the agent always initiates.
- **Plugins** (in-core Python) are called directly by the core when state changes. No polling. The core can reach them (they're in the same process), so polling would be wasted work.

**Why pull for agents:**

- Self-heals through reboots, sleep, and network changes. Push-first fails silently when a host is unreachable at command time.
- Within-the-tick latency (≤60s) is acceptable for screentime; sub-second isn't needed.
- Push (long-polling / SSE) remains additive if ever required: it's just a way to make agents poll sooner.

**Why event-driven for plugins:**

- The core can call them directly. Polling across an in-process function call is silly.
- Reconciliation runs as a background task scheduled by the originating API request — the operator's `curfew lock kid1` returns as soon as the database write commits; plugins reconcile in the background. Slow or failing plugins don't block the operator. Failures are logged and recorded in the audit log.
- A slow safety-net resync (default every 5 minutes) re-walks all governed users and re-calls each plugin, catching any missed events from restarts.

## ADR-004: Agents distributed via bootstrap script

**Decided:** agents (`windows-agent`, future `macos-agent`, etc.) install via a one-line shell/PowerShell bootstrap that drops the agent code + registers a scheduled task / launchd / cron job. The agent self-updates from the core via versioned manifests (ADR-009).

**Rejected:**

- **MSI / proper installer** — overkill for a homelab with a handful of PCs.
- **Group Policy / Intune / MDM** — requires AD or cloud MDM the homelab typically doesn't run.
- **Manual copy + manual scheduled task setup** — fine for one PC, doesn't scale.

**Why bootstrap won:** zero new infrastructure (the bootstrap and agent live behind the same Traefik that serves the API), one-time per device, idiomatic for the scale, self-healing updates via ADR-009.

(Plugins don't need this — they're files on disk in the curfew-core deployment, not remote installs.)

## ADR-005: Two extension contracts — agent and plugin

The two extension surfaces have different physics (per ADR-003), so they have different contracts. Both reduce to a *reconciler* — given a lock status, do the right thing — but everything around the reconciler differs.

### Agent contract (per-device, polling)

A device-level agent authenticates with a per-device bearer (ADR-006), heartbeats every tick to `POST /v1/devices/{device}/heartbeat` carrying its last-known state hash, pulls `GET /v1/devices/{device}/state` when the hash differs, and runs its reconciler against the result. May also `POST /v1/devices/{device}/activity` with `{user, occurred_at}` when it observes recent user input. One agent per device — multiple things to enforce on the same device share one process and one heartbeat.

### Plugin contract (in-core, event-driven)

A plugin is a Python class subclassing `Plugin` from the plugin SDK. Curfew-core discovers it from any directory in `CURFEW_PLUGINS_DIRS` at startup (ADR-013), instantiates it with operator-supplied config, and schedules its `reconcile(scope)` method as a background task when state changes for a user the plugin governs. No polling, no heartbeat, no auth. Failures are logged and audited but don't fail the originating API request. A slow safety-net resync re-calls reconcile periodically to catch missed events.

**Why two contracts:**

- The core can call plugins directly but can't call agents (NAT, sleep, no inbound). Forcing one contract on both means polluting one with concerns from the other.
- New agents = new SDK consumer + bootstrap (first-party — no third-party agent extension model). New plugins = new folder in any `CURFEW_PLUGINS_DIRS` entry. Different distribution stories naturally; trying to unify them adds friction without value.
- Schedule and budget evaluation live in the *core* as rules (ADR-008), not in agents or plugins. Both stay dumb — they see only `{locked, reasons}` for the relevant scope and don't need to understand why.

## ADR-006: Per-device bearer tokens for agents

**Decided:** each device with an agent installed gets its own bearer token. The `gamingrig` PC has a separate token from `laptop2`. Tokens are hashed at rest in the `agent_tokens` table.

**Why:** revoke a single compromised PC without nuking the rest; log which device made which call.

**Why per-device, not per-instance-of-something-smaller:** the practical revocation case is "this PC is compromised; cut it off." The whole agent on that device gets revoked, regardless of which reconcilers it runs. Per-something-smaller tokens are a complication without a real use case.

**Plugins don't have tokens.** Plugins run in-process; there's no network boundary to authenticate. The core just calls their reconciler directly.

**Read-scope tightening** — making `gamingrig`'s token only able to read its owner's slice of state, not all state — is a future feature on top of the kernel. Issuance is per-device from day one; scoping is later.

## ADR-007: Inventory tables declare expected agents and plugins

**Decided:** desired state is declared in the database; runtime data either projects from it or is irrelevant.

- **Agents:** `device_agents(device, type, config)` declares what's expected to be running on each device. `agent_instances(device, last_heartbeat, last_seen_version)` is a derived projection — created and deleted in the same transaction as the `device_agents` row. The instance row holds runtime fields; the assignment row holds desired state. The core records `last_heartbeat` on each tick and exposes it on `GET /v1/agents`. The kernel deliberately does *not* paint stale heartbeats as an alert — a powered-off device looks identical to a broken agent without independent reachability evidence (see the Reachability monitoring feature in PLAN.md).
- **Plugins:** `plugins(type, instance_id, config, governs, paused)` declares which plugins are assigned and how. There's no separate runtime-state table because plugins are in-process — their liveness *is* the core's. An assigned-but-paused plugin has its row but is skipped during reconciliation.

**Rejected:**

- **Pure self-announcement** (agent announces itself on first heartbeat; no database declaration). Loses the ability to distinguish "agent broken" from "agent uninstalled" — both look like silence.
- **Pure declarative for agents** (no heartbeats). Loses the liveness signal — operator never knows whether the agent is actually running.

**Why `governs` lives on `plugins` only:** an agent is bound to a device, and the device's `owner` determines the user it governs (or operates at device scope when `owner` is null). Plugins aren't bound to a device — `governs` is an explicit list (`["kid1", "kid2"]` or `["*"]`) because there's no implicit device-to-user link.

## ADR-008: Lock status is a scoped rule pipeline

**Decided:** the kernel computes lock status per scope. Two scopes ship from day one:

- **User scope**: `GET /v1/users/{user}/status` returns `{locked, reasons}` for that user.
- **Device scope**: `GET /v1/devices/{device}/status` returns the same shape for a device (used by the shared-device-lock feature and any future device-only locking semantics).

Rules implement a single interface, declare their scope at registration, and return zero or one reason. `locked` is the OR of registered rule outputs in that scope; `reasons` is the array of structured reason objects that fired.

**Reasons are structured objects, not strings:** `{kind: "manual_lock"}`, `{kind: "budget_exhausted", consumed: 120, budget: 90}`. Adding detail to a reason kind is a non-breaking change. Clients render `kind` and ignore unknown fields.

**Rejected:**

- **Hardcoded if/else.** Touches one central function for every new feature; regression risk per change. Call signature and response shape drift as features arrive.
- **Single-scope (user-only) pipeline.** Forces the shared-device-lock feature to add a new endpoint anyway, breaking the "features are pure additions" promise.
- **String reasons.** Either you encode detail inside the string and parse it, or you migrate to structured later. Pay once now.

**Why a scoped pipeline:**

- Every new lock condition (schedule, budget, shared-device, future per-device locks) registers a rule into the right scope. No API surface changes; no central code to touch.
- Plugins always see the same response shape regardless of which rules are registered. They don't need to know schedule expressions or budget math exist.
- The `reasons` array makes "why is this user (or device) locked?" trivially observable in UI and audit log.
- Rule tests don't entangle with each other.

## ADR-009: Agent updates are versioned and hash-verified

**Decided:** agents fetch their code via a manifest endpoint (`GET /v1/agents/{type}/manifest` → `{version, sha256, url}`). The agent verifies the SHA-256 of the fetched artifact matches the manifest before swapping atomically. Update checks happen on a slow tick (default hourly); state polling on the fast tick (default 60s).

**Rejected:** "fetch latest `agent.ps1` every minute and execute." Trivially exploitable: anyone who controls the core's static-file path (or MITMs HTTPS without certificate pinning) gets remote code execution on every managed PC, with whatever privileges the agent runs at — admin-equivalent for `windows-agent` (it modifies NTFS ACLs and registry).

**Why this is a kernel commitment, not a feature:**

- The agent fetch path is a remote-code-execution channel from the core to every privileged agent. There is no acceptable version of this without integrity checks.
- Versioning makes rollbacks possible and update cadence explicit; hash verification closes the MITM gap with no signing infrastructure required.
- Adding versioning + hash verification later means refitting every existing agent's update path. Cheaper to ship it once.

**Future tightening (a feature, not the kernel):** manifest signing with a long-lived private key on the core and a public key embedded in the bootstrap. Closes the gap if the core itself is partially compromised.

## ADR-010: Two SDKs in the kernel — agent SDK and plugin SDK

**Decided:** the kernel ships two SDKs, one for each extension surface.

### Agent SDK (polling, heartbeats, self-update)

Two language flavours sharing one contract:

- `curfew_agent_sdk_python` — for `macos-agent` and any future Linux/macOS agent.
- `curfew-agent-sdk-powershell` — for `windows-agent` and any future Windows agent.

Both handle: polling loop, heartbeat dispatch with retry/backoff, hash-based change detection (ADR-012), versioned + hash-verified self-update (ADR-009), error reporting back to core, config bootstrap. An agent author writes the reconciler; the SDK handles the rest.

### Plugin SDK (in-process, just enough)

Python only. Much smaller — no polling, no heartbeat, no self-update. Provides:

- A `Plugin` base class with a `reconcile(scope)` method to override
- Manifest validation helpers (resolve `config_schema` to a Pydantic model)
- Common types: `LockStatus`, `Reasons`, `ReconcileResult`
- Optional helpers for HTTP clients (timeout/retry) since plugins almost always call out to some external service

A plugin author writes a Python class. The plugin SDK provides types and helpers; the rest is the plugin's business logic.

**Rejected:** each agent or plugin rolls its own loop / contract.

**Why kernel rather than feature:**

- Multiple expected agents and plugins → multiple bug surfaces for the same boilerplate. Inconsistent retry semantics, inconsistent self-update mechanics, inconsistent error reporting are the predictable outcome.
- The SDKs *shape* their respective contracts. Adding them later effectively rewrites the contracts retroactively. Cheaper to ship them once and have every agent and plugin conform from day one.
- The reference test consumers (`reftest_agent` for the agent SDK, `reftest_plugin` for the plugin SDK) stress-test both contracts before any feature-level work begins.

## ADR-011: Config and settings are separate surfaces

**Decided:** the curfew-core API service has two surfaces for tunable values:

- **Config** is boot-time, env-and-file driven, immutable for the process. Loaded in precedence order: process env > `.env` (loaded into env) > `config.json` > built-in defaults. Implemented via Pydantic v2 `BaseSettings`. Used for values needed *before* the database opens (db path, listen address, root token).
- **Settings** are runtime-mutable, stored in a single-row `settings` table, edited through the API/CLI. No restart required. Used for operational knobs (tick rates, retention windows, thresholds).

**Rejected:**

- **Everything in env.** Operators can't change values without a redeploy; no audit trail of who changed what; settings drift across replicas if scaled.
- **Everything in the database.** The server can't open the database until it knows where it lives — at minimum `CURFEW_DB_PATH` has to exist before the database does.
- **Single config file with both startup and runtime knobs.** Same chicken-and-egg as above for db path; plus runtime mutation of a JSON file by multiple writers (CLI + future GUI) reintroduces the locking problems ADR-002 rejected.

**Why split:**

- The chicken-and-egg cleanly resolves: config is what's needed to *open* the database; settings are what's stored *in* the database.
- Operators can change tick rates and thresholds via the CLI/GUI without redeploys, and writes go through the audit log so the change is recorded.
- Secrets stay in env; secrets never touch `state.sqlite`. `config.json` can be committed or templated since it never holds the root token.

**Convention:** `.env` is for local-dev convenience (loaded automatically if present). Production deployments pass env vars directly via docker-compose. `config.json` is for non-sensitive deployment config that benefits from version control.

## ADR-012: Agent heartbeats carry a state hash for change detection

**Decided:** every agent heartbeat (`POST /v1/devices/{device}/heartbeat`) carries the agent's last-known state hash plus an optional `errors` array of `{kind, message, occurred_at}` objects (any local failures the agent wants to surface — reconcile exceptions, OS calls that returned errors, etc.); the server returns its current hash plus a small set of immediate-effect settings (`agent_tick_seconds`, etc.). If the hashes match, the agent has nothing new to do. If they differ, the agent fetches full state via `GET /v1/devices/{device}/state` and re-runs the reconciler. Reported errors are recorded in the audit log.

This applies to **agents only** (the polling extension surface). Plugins are in-process and don't poll — they're called directly when state changes (per ADR-003), so there's no heartbeat path to optimise.

**What the hash covers** (computed server-side, deterministic, per device):

- The device's owner's lock status (`{locked, reasons}` for the user).
- For shared devices: the device's lock status from the device-scope rule pipeline.
- Per-device agent config (`device_agents.config`).
- The relevant slice of the app catalog (entries referenced by the governed user's `target_apps`).
- Operational settings the agent reads.

Any operator action that affects the device — lock toggle, settings change, config edit, app-list edit — eventually changes the hash. The kernel doesn't try to invalidate hashes proactively; the hash is a content digest.

**Rejected:**

- **Always send full state in the heartbeat.** Wasteful on the hot path; heartbeats run every ~60s and state changes maybe a few times a day.
- **Force agent restart to pick up changes.** No remote-management story; defeats settings being runtime-mutable.
- **Push (SSE / long-polling) instead of hash polling.** Orthogonal — push is about latency, hashing is about heartbeat payload size. They compose: a future push channel could deliver new hashes proactively.

**What this also solves:** settings propagation. Changing a setting changes the hash; the next heartbeat surfaces the diff. The agent picks up new tick rates and behaviour without a restart.

## ADR-013: Plugins are drop-in Python modules in `CURFEW_PLUGINS_DIRS`

**Decided:** in-core plugins are Python packages dropped into any directory listed in `CURFEW_PLUGINS_DIRS` — a colon-separated list (PATH-style). Curfew-core scans each directory in order at startup, reads each subdirectory's `manifest.toml`, optionally installs `requirements.txt` into the shared Python environment, imports `plugin.py`, finds the class subclassing `Plugin`, and registers it under its `type` name. If the same `type` is declared in multiple dirs, later entries win.

The default value of `CURFEW_PLUGINS_DIRS` is the curfew repo's `plugins/` directory — so curfew ships with `adguard`, `smart-plug`, etc. discoverable out of the box, and operator-authored plugins drop in alongside them. Operators who prefer keeping their plugins outside the repo can append additional paths.

This follows the pattern used by Home Assistant `custom_components`, MkDocs entry points, Django apps, pytest plugins via pluggy, and similar Python-extensible frameworks.

```
plugins/
├── adguard/                  # ours
│   ├── manifest.toml         # type, name, version, config_schema, description
│   ├── plugin.py             # class AdGuardPlugin(Plugin): async def reconcile(...)
│   └── requirements.txt      # optional pip deps
└── wyze/                     # operator-authored
    ├── manifest.toml
    ├── plugin.py
    └── requirements.txt
```

**Rejected:**

- **Sidecar containers (one docker container per plugin, HTTP between core and plugin).** Higher friction for plugin authors (write a docker image + an HTTP server) without a corresponding benefit at homelab scale. The "language flexibility" argument doesn't apply for the audience curfew serves.
- **Pip-installed plugins via setuptools entry points.** Standard but requires rebuilding the curfew-core image to add a plugin. Defeats the "drop a folder, restart" UX that the operator should expect.
- **Hot-reload on file change.** Adds significant complexity (module unloading is tricky in Python) without enough payoff. Restarting curfew-core to pick up new plugin code is a few seconds; toggling existing plugins (pause / unpause) doesn't need a restart.
- **Per-plugin Python sub-environments.** Python doesn't actually support per-module dependency isolation in one process — `sys.modules` is global, and pip-install with `--target` plus `sys.path` munging is fragile (transitive deps clobber each other). The honest model is one shared environment.

**Trade-offs accepted:**

- **Shared Python environment.** All plugins share curfew-core's process and its installed packages. Conflicting `requirements.txt` versions are the operator's problem to resolve (pick a compatible version range, or vendor inside the plugin). For curfew's intended plugin set this is fine; the realistic plugins target common HTTP libraries with overlapping needs.
- **No crash isolation.** A plugin bug can crash curfew-core. Mitigated by catching exceptions at the `reconcile()` boundary and converting them to logged errors. Plugins doing genuinely unsafe things (importing bad C, hanging the event loop, unbounded memory use) can still cause problems — but for the audience (operator-vetted Python), the realistic failure surface is "the HTTP call to AdGuard returned 500."
- **Python only.** Plugin authors who want a different language are out. For homelab plugins, Python is fine; if a use case for non-Python plugins ever shows up, the sidecar/HTTP shape can be added as an alternative without removing the drop-in shape.

**Why this is a kernel commitment:** the plugin discovery model is part of the plugin contract. Operators install plugins by dropping folders into a `CURFEW_PLUGINS_DIRS` entry; plugin authors structure their code around `manifest.toml` + `plugin.py`. Changing this later means rewriting every plugin.
