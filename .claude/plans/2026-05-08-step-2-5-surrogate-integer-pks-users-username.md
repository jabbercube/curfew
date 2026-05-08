# Step 2.5 — Surrogate INTEGER PKs + `users.username`

## Context

PR #5 landed the storage kernel with **slug** as the PK on `users`, `devices`, `apps` (per PLAN.md's original shape). On review the user asked whether using strings as PKs is typical; the honest answer was no — the conventional pattern is a surrogate `id` PK plus a `UNIQUE` indexed handle column. After discussing int vs UUID, the decision is **`INTEGER PRIMARY KEY` autoincrement** for every PK, with the human handle (slug or username) demoted to a `UNIQUE NOT NULL` indexed column.

User also asked to rename the user handle from `slug` to `username` — more accurate for that table specifically (devices and apps still use `slug`).

This change happens **before any production data exists**, so the right shape is to edit `0001_initial.py` in place rather than add a `0002_*` ALTER migration. PR #5 is merged but no operator has deployed against the schema; the dev story is `rm state.sqlite && alembic upgrade head`. Once the project ships somewhere real, the "never edit released migrations" rule kicks in. We're not there.

User confirmed during planning to proceed with: int PK everywhere, `users.username`, deferring SQLModel `relationship()` declarations and `ON DELETE` policies to when CRUD lands. `agent_tokens.id` flattens from UUID to int (the UUID was a weak choice — the bearer secret is the credential, not the row id).

## Scope

| Table | Before | After |
|---|---|---|
| `users` | `slug` PK | `id` PK (int autoincrement) + `username` UNIQUE NOT NULL |
| `devices` | `slug` PK, `owner` FK→users.slug | `id` PK + `slug` UNIQUE, `owner_id` FK→users.id |
| `apps` | `slug` PK | `id` PK + `slug` UNIQUE |
| `agents` | `device` PK+FK→devices.slug | `device_id` PK+FK→devices.id |
| `agent_tokens` | `id` UUID PK | `id` int PK; `token_hash` UNIQUE unchanged |
| `user_locks` | `user` PK+FK→users.slug | `user_id` PK+FK→users.id |
| `plugins` | composite (type, instance_id) | unchanged |
| `audit_log` | `id` int PK, `target_id` string | unchanged. `target_id` stays a string — values for `target_kind=USER` are now usernames. |
| `manifests` | `type` PK | unchanged |
| `settings` | `id` int CHECK(id=1) | unchanged |

**FK column convention:** `*_id` suffix throughout (`owner_id`, `device_id`, `user_id`).

**Stays put:**
- `plugins.users` (the JSON list column) keeps its name. Values change semantically (now usernames, formerly slugs); same column.
- `audit_log.target_id` stays string. Audit log values survive deletion of their target and are read by humans.
- Composite PK on `plugins`. `type` PK on `manifests`. CHECK constraint on `settings`. Existing UUID import in `models.py` is removed.

**Deferred (per user confirmation):**
- SQLModel `relationship()` declarations — added when CRUD lands and access patterns are visible.
- `ON DELETE` cascade/restrict/set-null policies — per-relationship calls best made when CRUD lands.

## Files to modify

- `core/curfew/models.py` — restructure six tables (`User`, `Device`, `App`, `Agent`, `AgentToken`, `UserLock`). Add `id: int = Field(default=None, primary_key=True)` to each. Demote slug/username to `Field(unique=True, index=True)`. Rename FK columns to `*_id` and retarget to the parent's `id`. Drop `from uuid import UUID, uuid4` (no longer needed).
- `core/curfew/__init__.py` — no public-API change; same exports.
- `api/migrations/versions/0001_initial.py` — replace contents to match the new model shape. Procedurally: delete the file, run `cd api && uv run alembic revision --autogenerate -m "initial schema"`, then hand-clean the generated file to (a) replace `sqlmodel.sql.sqltypes.AutoString` with `sa.String` (drop the sqlmodel import), (b) add the `INSERT INTO settings` seed at the end of `upgrade()`. Filename stays `0001_initial.py`; revision id stays `"0001"`.
- `core/tests/test_models.py` — update field names (`User(slug=...)` → `User(username=...)`, `Device(owner="kid1")` → `Device(owner_id=user.id)` after a `flush()`). The existing flush-between-FK-deps pattern continues to work; the `.id` lookup is added inline.
- `api/tests/test_migration.py` — `test_devices_owner_fk_to_users` updates to `constrained_columns: ["owner_id"]` / `referred_columns: ["id"]`. Other migration tests (table presence, settings seed, indexes, downgrade) unaffected.
- `docs/PLAN.md` — Tables section: the rows for `users`, `devices`, `apps`, `agents`, `agent_tokens`, `user_locks` reflect the new id-PK + UNIQUE-handle shape. Data-model concepts: in the **Users** field table, change `slug` to `username` with the same description ("Stable identifier used in CLI/API/UI. Short URL-safe string (`kid1`)"). The `Devices` field table keeps `slug`. API examples (`/v1/users/{user}/...`) keep `{user}` as the path parameter name; the value is a username.
- `docs/DECISIONS.md` — append **ADR-015: Surrogate INTEGER PKs + UNIQUE handle columns**. Captures: decided int over UUID and over slug-as-PK; rejected UUID (no distributed-write story, gives up SQLite's `INTEGER PRIMARY KEY` rowid alias) and slug-as-PK (rename cascades through every FK; one column doing two jobs); why now (pre-prod, cheapest moment); references PR #5's earlier `name → slug` rename which was a stepping-stone to this shape.

## Implementation steps

1. Branch off `main`: `int-pks`. (PR #5 is merged.)
2. Edit `core/curfew/models.py` — six table classes. Drop UUID import.
3. Regenerate `0001_initial.py`: delete the file, `cd api && uv run alembic revision --autogenerate -m "initial schema"`, hand-clean (rename to `0001_initial.py`, set `revision = "0001"`, swap `sqlmodel.sql.sqltypes.AutoString` for `sa.String`, drop sqlmodel import, add settings seed).
4. Update `core/tests/test_models.py` and `api/tests/test_migration.py`.
5. Update `docs/PLAN.md` (Tables + Users field table).
6. Append ADR-015 to `docs/DECISIONS.md`.
7. Run the verification battery (below).
8. Commit, push branch, open PR #6.

## Verification

End-to-end checks before opening the PR:

1. `uv sync --all-packages --group dev` — clean.
2. `uv run python -c "from curfew import User, Device, App, Agent, AgentToken, Plugin, UserLock, AuditLog, Manifest, Settings"` — all importable.
3. `uv run ruff check .` and `uv run ruff format --check .` — clean.
4. `uv run mypy` — clean.
5. `uv run pytest` — all 27 tests pass (model tests adapted, migration tests adapted).
6. `uv run pre-commit run --all-files` — clean.
7. `cd api && rm -f /tmp/curfew-test.sqlite && CURFEW_DB_PATH=/tmp/curfew-test.sqlite uv run alembic upgrade head` — applies cleanly. Then:
   - `sqlite3 /tmp/curfew-test.sqlite '.schema users'` — confirms `id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE`.
   - `sqlite3 /tmp/curfew-test.sqlite '.schema devices'` — confirms `owner_id INTEGER` FK → `users(id)`.
   - `sqlite3 /tmp/curfew-test.sqlite 'SELECT * FROM settings'` — `1|60|3600|300|30|90`.
8. `uv run alembic downgrade base` — only `alembic_version` remains.
9. `git push origin int-pks` → `gh pr create` → CI green on the PR.

## Critical files for the implementer to reference

- `core/curfew/models.py` (current state — six tables to rewrite, four to leave alone).
- `api/migrations/versions/0001_initial.py` (current state — replaced wholesale).
- `core/tests/test_models.py` lines using `slug=`/`owner=`/`user=`/`device=` (mechanical substitutions).
- `api/tests/test_migration.py::test_devices_owner_fk_to_users` (column name assertions).
- `docs/PLAN.md` lines 63–76 (Tables) and lines 82–87 (Users field table).
- `docs/DECISIONS.md` after ADR-014 (where ADR-015 appends).

## Branch + PR

- Branch: `int-pks` off `main`.
- Single commit covering models + migration + tests + docs + ADR-015.
- Open PR #6.
