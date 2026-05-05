# Architecture decisions

Short notes on the major design decisions and rejected alternatives. Captures the *why* so we don't relitigate later.

## ADR-001: API-first hosted core

**Decided:** run a small FastAPI service in a docker container as the system of record. CLI is a thin HTTP client; future GUI is another HTTP client; plugins are HTTP clients too.

**Rejected:** pure-library design (CLI imports core functions directly, writes to a file on disk).

**Why:**

- A web GUI accessible from a phone is a stated long-term goal. Phone access requires a server.
- Multiple concurrent writers (CLI + GUI + plugins heartbeating) need a daemon to serialize. Library + file on disk means rolling our own locking — easy to get wrong.
- Future features (heartbeats, budgets, audit log, push) all need persistent compute beyond a static file.
- Docker is already the homelab pattern; one more compose stack is genuinely cheap operational cost.

Library-first remains the right call only if the project ever scopes back to terminal-only with no remote access. Not the plan.

## ADR-002: SQLite is the only persistent store

**Decided:** all persistent data — inventory, runtime state, tokens, audit log, agent manifests — lives in a single `state.sqlite` file. SQLModel for the schema, Alembic for migrations from day one.

**Rejected:**

- **Multi-store split (e.g. YAML inventory + SQLite runtime).** Felt natural early on — inventory is human-edited, runtime is machine-written, different lifecycles. Breaks down the moment a CLI or GUI tries to write inventory: YAML round-tripping that preserves comments and ordering is fragile, and bidirectional sync between YAML and a runtime store is famously hard. Cleaner to put everything in SQLite and offer YAML as an *optional client-side import tool* if anyone wants it (just another HTTP client of the inventory CRUD endpoints).
- **Postgres.** Overkill for a homelab single-tenant store. Adds an operational dependency.
- **JSON file.** Concurrency-unsafe with multiple writers. Schema-less, so every change is a custom migration loader.

**Why SQLite is enough:**

- WAL mode handles concurrent readers + a writer cleanly.
- Backups are `cp` of one file (or `.backup` for atomic).
- Inspection is `sqlite3 state.sqlite '.dump'` or any GUI.
- SQLModel gives Pydantic-compatible models with `create_all()`; Alembic versions migrations from day one so feature additions are additive, not panicked ports.

## ADR-003: Pull-based plugin reconciliation

**Decided:** plugins poll the API on a timer (default 60s) and reconcile their slice of state. Push (long-polling / SSE) is not planned.

**Why:**

- Self-heals through reboots, sleep, and network changes. Push-first fails silently when a host is unreachable at command time.
- Within-the-tick latency (≤60s) is acceptable for screentime. Sub-second response isn't needed.
- Push remains additive if ever required: it's just a way to make plugins poll sooner; the reconcile path doesn't change.

## ADR-004: Per-device plugins distributed via bootstrap script

**Decided:** plugins that need OS-level access (`windows-pc`, future `macos-pc`) install via a one-line shell/PowerShell bootstrap that drops the agent + registers a scheduled task / launchd / cron job. The agent self-updates from the core via versioned manifests (ADR-009).

**Rejected:**

- **MSI / proper installer** — overkill for a homelab with a handful of PCs.
- **Group Policy / Intune / MDM** — requires AD or cloud MDM the homelab doesn't run.
- **Manual copy + manual scheduled task setup** — fine for one PC, doesn't scale.

**Why bootstrap won:** zero new infrastructure (the bootstrap and agent live behind the same Traefik that serves the API), one-time per device, idiomatic for the scale, self-healing updates via ADR-009.

## ADR-005: Plugin contract — poll, reconcile, heartbeat

**Decided:** a plugin authenticates with a per-instance bearer (ADR-006), reads its scoped state (`GET /v1/state/effective/{user}` or filtered variants), reconciles its slice of the world to match, and `POST`s a heartbeat each tick. Optionally `POST`s an activity ping when there's recent user input (consumed by the budget feature when registered).

**Why minimal:**

- Lets in-core plugins (`adguard`, `smart-plug`) and per-device plugins (`windows-pc`) share the same contract — deployment differs, contract doesn't.
- New plugin = new SDK consumer + reconcile logic. Nothing in the core changes.
- Schedule and budget evaluation live in the *core* as rules (ADR-008), not in plugins. Plugins stay dumb — they see only `effective_lock: bool` and a `reasons` list, no understanding of why.

## ADR-006: Per-instance bearer tokens

**Decided:** each plugin instance gets its own bearer token. A second `windows-pc:laptop2` would have a separate token from `windows-pc:gamingrig`. Tokens are hashed at rest in the `plugin_tokens` table.

**Why:** revoke a single compromised PC without nuking the rest; log which instance made which call.

**Read-scope tightening** — making a `windows-pc:gamingrig` token only able to read `kid1`'s slice of state, not all state — is a future feature on top of the kernel. Issuance is per-instance from day one; scoping is later.

## ADR-007: Inventory declares expected plugin instances

**Decided:** the inventory tables (`device_plugins` for per-device, `core_plugins` for in-core) are the source of truth for which plugin instances *should* exist. Plugin heartbeats record what *actually* checked in. The core compares the two and surfaces drift on `GET /v1/plugins`.

**Rejected:**

- **Pure plugin-driven** (plugin announces itself on first heartbeat; inventory has no plugin assignment). Loses the ability to distinguish "agent broken" from "agent uninstalled" — both look like silence.
- **Pure inventory-driven** (declared in inventory, no heartbeats). Loses liveness signal entirely.

**Symmetric for in-core and per-device plugins:** both are inventory entries with config, both produce heartbeats, both surface drift the same way. The contract doesn't care where the agent runs.

## ADR-008: Effective state is a rule pipeline

**Decided:** `GET /v1/state/effective/{user}` returns `{locked: bool, reasons: [...]}`. The `locked` value is the OR of registered rule outputs; the `reasons` array names which rules fired. Rules implement a single interface and register at startup.

**Rejected:** hardcoded if/else in the lock computation. Each new feature would touch the central function and create regression risk; the call signature and response shape would drift as features arrive.

**Why a pipeline:**

- Every new lock condition (schedule, budget, shared-device) is a new rule registered into the same machinery. No API or schema reshape.
- Plugins always see the same response shape regardless of which rules are registered. They don't need to know schedule expressions or budget math exist.
- The `reasons` array makes "why is this user locked?" trivially observable in the UI and the audit log.
- Tests for one rule don't entangle with another.

## ADR-009: Agent updates are versioned and hash-verified

**Decided:** agents fetch their code via a manifest endpoint (`GET /v1/agents/{type}/manifest` → `{version, sha256, url}`). The agent verifies the SHA-256 of the fetched artifact matches the manifest before swapping atomically. Update checks happen on a slow tick (default hourly); state polling on the fast tick (default 60s).

**Rejected:** "fetch latest `agent.ps1` every minute and execute." Trivially exploitable: anyone who controls the core's static-file path (or MITMs HTTPS without certificate pinning) gets remote code execution on every kid PC, with whatever privileges the agent runs at — admin-equivalent for `windows-pc` (it modifies NTFS ACLs and registry).

**Why this is a kernel commitment, not a feature:**

- The agent fetch path is a remote-code-execution channel from the core to every privileged agent. There is no acceptable version of this without integrity checks.
- Versioning makes rollbacks possible and update cadence explicit; hash verification closes the MITM gap with no signing infrastructure required.
- Adding versioning + hash verification later means refitting every existing plugin's update path. Cheaper to ship it once.

**Future tightening (a feature, not the kernel):** manifest signing with a long-lived private key on the core and a public key embedded in the bootstrap. Closes the gap if the core itself is partially compromised.

## ADR-010: A plugin SDK is part of the kernel

**Decided:** the kernel ships a Python SDK (`curfew_plugin_sdk`) and a PowerShell module (`curfew-plugin.psm1`) that handle polling, heartbeating, retry/backoff, hash-verified self-update, and error reporting. A plugin's business logic is the reconciler — given the effective state, do the right thing. The SDK handles everything else.

**Rejected:** each plugin rolls its own loop.

**Why kernel rather than feature:**

- Six expected plugins → six bug surfaces for the same boilerplate. Inconsistent retry semantics, inconsistent self-update mechanics, inconsistent error reporting are the predictable outcome.
- The SDK *shapes* the plugin contract. Adding it later effectively rewrites the contract retroactively (every existing plugin migrates). Cheaper to ship it once and have every plugin conform from day one.
- `windows-pc` is the first SDK consumer and stress-tests the contract before any second plugin starts.
