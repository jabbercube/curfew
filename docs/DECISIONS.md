# Architecture decisions

Short notes on the major design decisions and rejected alternatives. Captures the *why* so we don't relitigate later.

## ADR-001: API-first hosted core (rejected: library-first / no-daemon)

**Decided:** run a small FastAPI service in a docker container as the system of record. CLI is a thin HTTP client. Future GUI is another HTTP client.

**Rejected:** pure-library design where the CLI imports `curfew` functions directly and writes state to a JSON file on disk. No daemon ever required. Frontends share the library.

**Why API-first won:**
- A web GUI accessible from a phone is a stated long-term goal. Phone access *requires* a server. Bolting one on later means refactoring file-locking and ad-hoc state mutation out of the CLI.
- Multiple concurrent writers (CLI + future GUI + scheduler) on a JSON file means rolling our own locking — easy to get wrong. A daemon serializes writes naturally.
- Heartbeats, budgets, audit logs (V3+) all need persistent compute beyond a static file.
- Docker is already the homelab pattern. One more small compose stack is genuinely cheap operational cost.
- Push (long-polling / SSE), if/when added, requires *something* running. The library-only path can never have it.

**Library-first remains the right call** if the project ever scopes back to terminal-only / desktop-only GUI with no phone access. Not currently the plan.

## ADR-002: Pull-based plugin reconciliation (push deferred)

**Decided:** plugins poll the API on a timer (default 60s) and reconcile their slice of state. Push (long-polling / SSE) deferred to V2+ as a pure addition.

**Why:**
- Self-heals through reboots, sleep, and network changes. Kid PCs are unreliable; a push-first design fails silently when the host is unreachable at command time.
- Push is purely additive once the contract is "poll + reconcile" — push just makes plugins poll sooner; same code path runs.
- Within-the-tick latency (≤60s) is acceptable for screentime. Sub-second response isn't needed.

## ADR-003: Per-device plugins distributed via bootstrap script

**Decided:** plugins that need OS-level access (`windows-pc`, future `macos-pc`) install via a one-line PowerShell/shell bootstrap that drops the agent + registers a scheduled task. The agent self-updates from the core thereafter.

**Rejected:**
- MSI / proper installer — overkill for a homelab with a handful of PCs.
- Group Policy / Intune / MDM — requires AD or cloud MDM the homelab doesn't run.
- Manual copy + manual scheduled task setup — fine for one PC, doesn't scale.

**Why bootstrap won:** zero new infrastructure (the bootstrap and agent files live behind the same Traefik that serves the API), one-time per device, self-healing updates, idiomatic for the scale.

## ADR-004: Plugin contract is "poll state, reconcile, optional heartbeat"

**Decided:** minimal interface. A plugin authenticates with a bearer token, calls `GET /v1/state` (or filtered variant) on a timer, reconciles its domain to match, and optionally `POST`s heartbeats so the core can show liveness in the UI.

**Why minimal:**
- Lets in-core plugins (AdGuard, smart-plug) and per-device plugins (windows-pc) share the same contract. Deployment differs; the contract doesn't.
- New plugin = new poller + new reconcile logic. Nothing in the core changes.
- Schedule and budget evaluation live in the *core*, not in plugins, so plugins stay dumb. Plugins see only `effective_lock: bool`.

## ADR-005: SQLite for runtime state from V1

**Decided:** V1 stores runtime state (user locks, plugin heartbeats, future budgets and audit log) in `state.sqlite`, with SQLModel for the schema and Alembic for migrations from day one.

**Rejected:** single JSON file written atomically by the API. Simpler at first glance, but:

- The API has multiple writers in V1 already — the CLI calling lock/unlock plus plugins heartbeating on a 60s tick. JSON-on-disk needs file locking (fcntl), easy to get wrong across Docker volume mounts. SQLite WAL mode handles this for free.
- The "migrate from JSON later" path is a one-way door under deadline pressure — the JSON-to-relational reshape would happen during V3 (budgets), competing with the feature work that motivated the move. Starting in SQLite means V3 just adds tables.
- After the first 2–3 schema changes, writing custom upgrade-on-load functions for JSON is more work than versioned Alembic migrations, not less.
- The original "JSON is human-readable" appeal is eliminated by ADR-007 — human-edited inventory now lives in `inventory.yaml`. Runtime state is API-written only and gains nothing from being a text file.

**Why this is still simple enough:** SQLModel gives Pydantic-style models with `create_all()`. The V1 schema is three small tables. Backups are `cp` of a single file. Inspection is `sqlite3 state.sqlite '.dump'` or any GUI.

## ADR-006: Per-plugin bearer tokens from day one

**Decided:** each plugin gets its own token, even though V1 only has one plugin (`windows-pc`).

**Why:** small upfront cost (one extra column in a config), big audit/security win later. Per-plugin tokens let us revoke a single compromised PC without nuking the rest, and we can log which plugin made which call.

## ADR-007: Inventory and runtime state are separate stores

**Decided:** persistent data is split across two stores with different lifecycles:

- **`inventory.yaml`** — users (with `role`), devices (with `owner`, `mac`, expected `plugins`), and the global `apps` catalog. Human-edited (or rewritten by a CLI command). Loaded on startup; reloaded via `POST /v1/admin/reload-inventory`. Validated against a Pydantic schema; bad YAML refuses to load and the previous good copy stays in memory.
- **`state.sqlite`** — runtime state written by the API on every command and every plugin tick: locks, heartbeats, last-seen, future budget tally and audit log.

**Rejected:** single combined store (JSON or SQLite) holding both inventory and runtime state.

**Why split:**
- Inventory and runtime state have fundamentally different lifecycles. Inventory changes when a kid gets a new PC (weekly at most); runtime state changes on every CLI command and every plugin tick (every 60s). Mixing them means every state write rewrites or touches data that didn't change.
- Inventory benefits from being human-editable and version-controlled. YAML beats SQLite for `vim` access and beats JSON for comments and multiline strings.
- Runtime state benefits from concurrency-safe writes and indexed queries (per ADR-005). Inventory does not — it's loaded once and held in memory.
- Backups have different cadences. `inventory.yaml` lives in a config dir (or git); `state.sqlite` lives in a docker volume backed up like other runtime data.
- Recovery: corruption of `state.sqlite` is recoverable from heartbeats + plugin reconciliation. Corruption of `inventory.yaml` is a real problem, but the file is tiny and version-controlled.

**Why YAML over TOML/JSON for inventory:** comments (essential for explaining MAC entries, plugin choices), multiline strings, clean nested lists. Loaded once at startup, so format performance is irrelevant.

## ADR-008: Plugin assignment is inventory-as-desired-state

**Decided:** the inventory file declares which plugin instances *should* exist; plugin heartbeats record what *actually* checked in; the core compares the two and surfaces drift on `GET /v1/plugins`.

Concretely, in V1:

- Each `devices.{name}.plugins` entry in `inventory.yaml` produces one expected plugin instance (e.g. `windows-pc:gamingrig`). In-core plugin types like `adguard` are configured separately and produce their own expected instances.
- Plugins authenticate with a per-plugin bearer (per ADR-006) and call `POST /v1/plugins/{name}/heartbeat` on every tick. The core records `last_heartbeat` and `last_seen_version`.
- `GET /v1/plugins` lists expected instances (from inventory) joined with actual heartbeats. Stale or missing heartbeats are visible there as drift.

**Rejected:**

- **Pure plugin-driven** (plugin announces itself on first heartbeat, inventory has no plugin assignment). Loses the ability to distinguish "agent broken" from "agent uninstalled" — both look like silence.
- **Pure inventory-driven** (inventory declares assignment, no heartbeats). Loses liveness signal.

**End-state model (target for later phases, not all in V1):**

- **Plugin types vs instances.** A *type* is the deployable unit (code, bootstrap, version). An *instance* is a runtime entity bound to inventory — `windows-pc` running on `gamingrig` governing `kid1`. Types live in a static registry in the core image; instances live in inventory.
- **Per-instance scoped tokens.** When you add `windows-pc` to a device's plugin list, the core mints a token scoped to that instance — it can read only that user's effective state and heartbeat only as that instance. V1 still uses per-plugin-type tokens (per ADR-006); per-instance scoping is a future tightening.
- **Type-level capability registry.** Each type declares which device `os` and `type` it applies to, which scopes it supports (`user`, `shared`), and which config keys it requires. Lets the (future) GUI offer constrained dropdowns instead of free-form strings.
- **Plugins as dumb executors.** A plugin reads its scoped state from the API, reconciles, heartbeats. It does not announce itself, does not carry config that only it knows about — config lives in its inventory record on the core.
- **Symmetric add/remove ritual.** Adding: edit inventory → core mints a token → operator runs bootstrap. Removing: edit inventory → core revokes token → next agent tick gets 401.

**Why this end-state:** single source of truth (inventory), clean authorization model (tokens scoped by inventory entries), symmetric onboarding for in-core and per-device plugins, and concrete UI/CLI semantics ("add plugin to device") rather than ambient discovery.

**Note for revisit:** this area is dense. The V1 cut is intentionally minimal so we can get the rest of the system live and revisit the model in flight before V2 ramps it up.

## What's still open

See the **Open questions** section of [PLAN.md](PLAN.md) — those are decisions deferred until V1 implementation begins.
