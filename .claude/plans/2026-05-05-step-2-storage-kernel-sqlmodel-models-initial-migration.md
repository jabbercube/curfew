# Step 2 — Storage kernel: SQLModel models + initial migration

## Context

The repo has scaffolding (PR #1, #2, #3) and design assets (PR #4) but **zero schema, zero models, zero migrations**. Every subsequent kernel slice (auth middleware, rule pipeline, every CRUD endpoint, agent state hash, plugin discovery, audit middleware) reads or writes the database. PLAN.md commits to "the schema is created in the initial migration with all fields the system will ever need — even fields no rule reads yet" — so this PR lands all 10 kernel tables in **one Alembic migration**, plus the SQLModel classes for them, plus the seed `settings` row with defaults. Not a feature-by-feature roll-out.

This is the highest-judgment piece of the kernel: once the schema is in, every later slice's API surface is constrained by it. Migrations against a populated database are far more expensive than getting the column shape right now. PLAN.md is explicit about this being the intent.

## Scope

**In:**
- 10 SQLModel classes in `core/curfew/models.py` covering every kernel table.
- One Alembic migration (`api/migrations/versions/0001_initial.py`) creating all tables + seeding the `settings` row.
- Alembic env wiring (`api/alembic.ini`, `api/migrations/env.py`, `api/migrations/script.py.mako`).
- Per-model unit tests in `core/tests/test_models.py` (CRUD round-trip, FK enforcement, JSON columns, enum values, `settings` singleton CHECK).
- One `api/tests/test_migration.py` exercising "apply migration on empty DB → tables exist + settings row seeded".
- Dep additions: `core/pyproject.toml` gains `sqlmodel`; `api/pyproject.toml` gains `alembic`.
- **PLAN.md doc updates** to match the schema deviations (rename `name`→`slug`, drop `LAPTOP`, rename `governs`→`users`, drop `target_apps`, rename `agent_url`→`url`, document `target_kind`/`target_id`, document `agent_tokens.id`).

**Out (next PR):**
- DB engine factory / session lifecycle (the runtime side).
- The FastAPI app skeleton, auth middleware, audit middleware.
- Any CRUD endpoint or rule.
- Async session support — V1 is **sync SQLAlchemy** (rationale: SQLite-WAL at homelab scale is fast enough; sync simplifies the stack; Alembic itself stays sync regardless. Async can be a future feature if request volume ever justifies it; the model layer doesn't need to change.)

## Schema deviations from PLAN.md

Three I proposed myself + four corrections from the user (2026-05-05 review). All applied. PLAN.md is updated to match where it conflicts (see "PLAN.md updates" below).

### 1. `manifests.agent_url` → `manifests.url`

PLAN.md line 75 names the column `agent_url`, but lines 388–389 show the API returning `{ version, sha256, url }`. Rename the column to `url` to match the API verbatim. The "agent_" prefix is redundant — the `manifests` table only holds agent manifests (PLAN.md is explicit about plugins not being distributed this way).

### 2. `agent_tokens` — add surrogate `id`, keep `token_hash` as unique non-PK index

PLAN.md uses `token_hash` as the PK. The CLI surface (PLAN.md line 369: `DELETE /v1/devices/{device}/tokens/{id}`) calls the management identifier `id`, and `curfew agent token list` returns "active token IDs (no secrets)". If `id == token_hash`, the management API leaks hashes through URL paths and list responses. Not a credential break — the bearer secret never touches the DB — but it's poor hygiene and confuses future log redaction.

Shape: `id` (UUID, PK) + `token_hash` (string, UNIQUE indexed) + `device` (FK) + `created_at` + `revoked_at`. The CLI's `id` is the UUID; the auth path still does `WHERE token_hash = ?` with the same single-index latency.

### 3. `audit_log.target` → `target_kind` + `target_id`

PLAN.md uses a single `target` column. Every realistic query is "all audit rows for *user* kid1" or "all audit rows for *device* gamingrig" — two-axis filtering. Splitting into `target_kind` (enum) + `target_id` (string) makes both axes indexable and structured.

Cost: one column more on a non-hot-path table. Worth it.

### 4. PK column rename: `name` → `slug` on `users`, `devices`, `apps`

The PK is a URL-safe identifier (`kid1`, `gamingrig`), not a display name. PLAN.md itself describes it as "Stable identifier used in CLI/API/UI. Short string (`kid1`), not a real name." `slug` is the accurate name; `name` invites confusion with future display-name columns (e.g. "Kid One — Family Account"). FKs follow: `devices.owner` references `users.slug`; `agents.device` and `user_locks.user` reference the slugs of their parents.

Renaming on a not-yet-existing schema is free; renaming after the API ships is an `ALTER TABLE` plus client churn.

### 5. Drop `DeviceType.LAPTOP` — laptop folds into `PC`

A laptop and a desktop PC are enforced identically (NTFS ACL, registry policy, process kill). The form-factor distinction has no behaviour in the kernel. `DeviceType` becomes `{ pc, phone, tablet, console, tv }`. If a future feature needs to distinguish them (battery thresholds? lid-close behaviour?) it can add the column then.

### 6. Defer `users.target_apps` (and the `users → apps` link)

User asked to "remove target_apps from user to begin with". The kernel ships **without** a per-user app list. Implications:

- The `apps` catalog table still exists (it's a global catalog of blockable executables and URLs).
- `User` has no link to apps in this PR. Manual lock fires, status returns `locked: true`, but agents have no per-user app list to consult yet.
- Adding it back later is a one-line migration: `ALTER TABLE users ADD COLUMN target_apps JSON DEFAULT '[]'`. The decision about *where* it lives (column on users vs. junction table vs. JSON rule_config — see PLAN.md "Per-rule user config — decision deferred" lines 89–95) is intentionally deferred to when the first concrete agent (`windows-agent`) lands and we know the access pattern.
- The reftest agent (which will land in a later PR) can hard-code an empty target list or read from device-level config.

### 7. `plugins.governs` → `plugins.users`

Cleaner. The column holds user slugs (or `["*"]`); calling it `users` says exactly that. CLI flag becomes `--users` instead of `--governs`. Plugin reconcile sees `scope.users` instead of `scope.governs` (or whatever the SDK names it).

## Other open decisions (recommendations, not pushback)

- **Single `models.py` vs split per-table files:** single file. ~250 lines total for 10 tables; splitting just adds import ceremony and `__init__.py` re-exports.
- **Enum storage:** Python `enum.Enum` (string values) + SQLAlchemy `Column(String)` with a CHECK constraint listing valid values. Avoids SQLite's lack of native ENUM, stays portable to PostgreSQL if we ever migrate, and gives Pydantic a clean enum type.
- **JSON columns:** SQLModel `Field(sa_column=Column(JSON))` with Python type `list[str]` or `dict[str, Any]`. SQLAlchemy + SQLite stores as TEXT; Pydantic round-trips cleanly.
- **Timestamps:** `DateTime(timezone=True)` everywhere. Defaults via `default_factory=lambda: datetime.now(timezone.utc)`. No naive datetimes.
- **`user_locks` row creation:** lazy. Absent row = `manual_lock = false`. Saves an INSERT on user creation; matches the rule's "treat missing as unlocked" semantics. The lock endpoint upserts.
- **`settings` singleton:** SQLite CHECK `id = 1`, plus app-layer assertion (defense in depth). Initial migration `INSERT INTO settings (id, agent_tick_seconds, ...) VALUES (1, 60, ...)`.
- **SQLAlchemy naming convention** in `MetaData`: standard naming for indexes/FKs/UQ/CK so Alembic auto-gen produces stable constraint names across machines. Pattern: `ix_%(column_0_label)s`, `uq_%(table_name)s_%(column_0_name)s`, `fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`, `pk_%(table_name)s`, `ck_%(table_name)s_%(constraint_name)s`.

## The 10 tables (final shape)

All paths are `core/curfew/models.py`. Ordering reflects FK dependencies for migration ops.

```python
# Enums
class UserRole(str, Enum): MEMBER, MANAGER, ADMIN
class DeviceType(str, Enum): PC, PHONE, TABLET, CONSOLE, TV    # LAPTOP folded into PC
class DeviceOS(str, Enum): WINDOWS, MACOS, LINUX, IOS, ANDROID
class AuditTargetKind(str, Enum): USER, DEVICE, APP, AGENT, PLUGIN, SETTINGS, MANIFEST

# Tables (in dependency order)

class User:
    slug: str = PK
    role: UserRole
    managed: bool = True
    # target_apps deferred; add later via ALTER TABLE when the first concrete agent lands

class App:
    slug: str = PK
    exe_paths: list[str] = []        # JSON
    process_names: list[str] = []    # JSON
    urls: list[str] = []             # JSON

class Device:
    slug: str = PK
    owner: str | None = FK(User.slug)    # nullable for shared devices
    type: DeviceType
    os: DeviceOS
    mac: list[str] = []              # JSON
    managed: bool = True

class Agent:
    device: str = PK + FK(Device.slug)   # one agent per device
    type: str                            # e.g. "windows-agent"
    config: dict[str, Any] = {}          # JSON; per-agent-type schema
    last_heartbeat: datetime | None = None
    last_seen_version: str | None = None

class AgentToken:
    id: UUID = PK
    token_hash: str = unique-indexed     # SHA-256 hex of the bearer
    device: str = FK(Device.slug)
    created_at: datetime
    revoked_at: datetime | None = None

class Plugin:
    type: str = PK (composite)
    instance_id: str = PK (composite, default "default")
    config: dict[str, Any] = {}
    users: list[str] = []            # JSON; user slugs or ["*"] (renamed from governs)
    enabled: bool = True

class UserLock:
    user: str = PK + FK(User.slug)
    manual_lock: bool = False
    set_at: datetime
    set_by: str | None = None        # actor identifier (root token == "operator", else user slug)

class AuditLog:
    id: int = PK (autoincrement)
    actor: str
    action: str                      # e.g. "user.lock", "plugin.assign"
    target_kind: AuditTargetKind
    target_id: str                   # the target's slug (or other identifier)
    payload: dict[str, Any] = {}     # JSON; action-specific
    occurred_at: datetime            # indexed for retention pruning

class Manifest:
    type: str = PK                   # one row per agent type (e.g. "windows-agent")
    version: str
    sha256: str
    url: str                         # renamed from agent_url

class Settings:
    id: int = PK + CHECK(id = 1)
    agent_tick_seconds: int = 60
    manifest_tick_seconds: int = 3600
    plugin_resync_seconds: int = 300
    plugin_reconcile_timeout_seconds: int = 30
    audit_retention_days: int = 90
```

Indexes added beyond PKs:
- `audit_log(occurred_at)` — retention pruning sweeps by date.
- `audit_log(target_kind, target_id)` — "all events for kid1".
- `agent_tokens(token_hash)` UNIQUE — auth-path lookup.
- `agent_tokens(device, revoked_at)` — list-active-tokens queries.

## File layout

```
core/
├── curfew/
│   ├── models.py             # NEW: all 10 SQLModel classes + enums + naming convention
│   └── __init__.py           # re-exports models for `from curfew.models import User`
└── tests/
    └── test_models.py        # NEW: CRUD round-trips, FK behaviour, JSON, singleton CHECK

api/
├── alembic.ini               # NEW: script_location = migrations, sqlalchemy.url = sqlite:///./state.sqlite (env-var override)
├── migrations/
│   ├── env.py                # NEW: imports core.curfew.models, sets target_metadata
│   ├── script.py.mako        # NEW: standard Alembic template
│   └── versions/
│       └── 0001_initial.py   # NEW: creates all 10 tables + INSERT into settings
└── tests/
    └── test_migration.py     # NEW: apply migration → reflect schema → assert tables + settings row
```

## Dep additions

- `core/pyproject.toml`: add `sqlmodel>=0.0.22`. (SQLModel pulls SQLAlchemy 2.0 + Pydantic v2 transitively.)
- `api/pyproject.toml`: add `alembic>=1.13`.
- `uv.lock`: regenerated by `uv lock`.

## Implementation steps

1. Branch off `main`: `storage-kernel`.
2. Add deps: edit `core/pyproject.toml`, `api/pyproject.toml`. Run `uv lock`.
3. Write `core/curfew/models.py` — all enums, all 10 SQLModel classes, MetaData naming convention.
4. Write `core/curfew/__init__.py` re-exports for the public API.
5. Bootstrap Alembic: `cd api && uv run alembic init --template generic migrations`. Replace generated `env.py` with a version that imports `core.curfew.models.SQLModel.metadata` as `target_metadata`. Set `alembic.ini`'s `script_location = migrations` and `sqlalchemy.url` to a default SQLite path overridable by `CURFEW_DB_PATH`.
6. Generate the initial migration: `uv run alembic revision --autogenerate -m "initial schema"`. Hand-edit the generated file: rename to `0001_initial.py`, add the `INSERT INTO settings` seed in `upgrade()`, verify the CHECK constraints + indexes are present.
7. Write `core/tests/test_models.py` (one test per model, plus singleton CHECK and FK behaviour).
8. Write `api/tests/test_migration.py` (`alembic upgrade head` against a temp-file SQLite, then reflect and assert).
9. **Update PLAN.md** — Tables row for users/devices/apps/plugins/manifests/audit_log/agent_tokens; Data-model concepts sections (User: drop `target_apps` row, rename `name`→`slug`; Device: drop LAPTOP from type enum, rename `name`→`slug`); Plugin lifecycle / CLI examples (`--governs` → `--users`); CLI shape table; "Per-rule user config — decision deferred" note (still applies, mentions `target_apps` will return there). **Update DECISIONS.md** ADR-006 reference to `agent_tokens` schema. Total: ~20 line edits across 2 files.
10. Run `uv sync --all-packages --group dev`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, `uv run pre-commit run --all-files`.
11. Commit, push branch, open PR #5.

## Verification

End-to-end checks:
1. `uv sync --all-packages --group dev` — clean.
2. `uv run python -c "from curfew.models import User, Device, App, Agent, AgentToken, Plugin, UserLock, AuditLog, Manifest, Settings; print('all import')"`.
3. `uv run mypy` — clean.
4. `uv run pytest` — all `core/tests/test_models.py` tests pass; the migration test passes.
5. `cd api && uv run alembic upgrade head --sql` — emits the expected DDL (sanity check the generated SQL).
6. `cd api && rm -f /tmp/curfew-test.sqlite && CURFEW_DB_PATH=/tmp/curfew-test.sqlite uv run alembic upgrade head` — apply against a real file. Then `sqlite3 /tmp/curfew-test.sqlite '.schema'` should show all 10 tables; `SELECT * FROM settings` should return one row with the documented defaults.
7. `uv run ruff check .` and `uv run ruff format --check .` — clean.
8. `uv run pre-commit run --all-files` — clean.
9. Push → CI green.

## Critical files for the implementer to reference

- `docs/PLAN.md` lines 63–119 (Tables + Data model concepts).
- `docs/PLAN.md` lines 315–325 (Settings table fields and defaults).
- `docs/PLAN.md` lines 329–337 (Auth + audit log).
- `docs/PLAN.md` lines 380–391 (API surface — informs `users.role`, `device.type`, `device.os`, `audit_log.action` enum values).
- `docs/DECISIONS.md` ADR-002 (SQLite + SQLModel + Alembic from day one).
- `docs/DECISIONS.md` ADR-006 (per-device bearer tokens, hashed at rest).
- `docs/DECISIONS.md` ADR-007 (`agents` table single-row-per-device with declarative + runtime columns).
- `core/pyproject.toml`, `api/pyproject.toml` (where deps land).

## Branch sequencing

- Branch: `storage-kernel` off `main`.
- Single commit covering: deps, models, migration, Alembic config, tests.
- Open PR #5.
- Quick-win parallel PRs (LICENSE, dependabot, CONTRIBUTING) can come before, after, or alongside — not blocked.
