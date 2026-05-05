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

## ADR-005: State backend starts as JSON, migrates to SQLite at V3

**Decided:** V1 stores state in a single JSON file (simple, easy to inspect/back up). Migrate to SQLite when V3 (budgets) lands, since budget tallying needs efficient append + query.

**Why staged:** JSON is the simplest thing that could possibly work for V1/V2. SQLite buys ACID and indexed queries when we actually need them — no premature complexity.

## ADR-006: Per-plugin bearer tokens from day one

**Decided:** each plugin gets its own token, even though V1 only has one plugin (`windows-pc`).

**Why:** small upfront cost (one extra column in a config), big audit/security win later. Per-plugin tokens let us revoke a single compromised PC without nuking the rest, and we can log which plugin made which call.

## What's still open

See the **Open questions** section of [PLAN.md](PLAN.md) — those are decisions deferred until V1 implementation begins.
