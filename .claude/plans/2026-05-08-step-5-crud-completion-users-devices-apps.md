# Step 5 — CRUD completion: users (LIST/PATCH/DELETE) + devices + apps

## Context

Step 4 (PR #11) shipped the lock feature with the bare minimum users CRUD (`POST /v1/users`, `GET /v1/users/{user}`) needed to exercise it. Step 5 finishes the operator-facing kernel data CRUD: every kernel data table (users, devices, apps) now has full CRUD and the operator can manage the system entirely through the API. This is the "operator can drive the kernel without touching SQLite directly" milestone.

Devices and apps both arrive net-new in this slice — they had no routes at all before. Users just gets its missing verbs filled in.

## Scope

**In:**
- `core/curfew/schemas.py` — `UserUpdate`, `DeviceCreate`/`DeviceRead`/`DeviceUpdate`, `AppCreate`/`AppRead`/`AppUpdate`. PATCH schemas have all-optional fields; routes call `model_dump(exclude_unset=True)` so a partial PATCH only touches what the client actually sent.
- `api/curfew_api/routes/users.py` — adds `GET /v1/users` (alphabetical), `PATCH /v1/users/{user}` (partial update), `DELETE /v1/users/{user}` (409 if the user owns devices; cascades the 1:1 user_lock).
- `api/curfew_api/routes/devices.py` — new file, full CRUD. API uses `owner: str | None` (username) and translates to/from `owner_id` via lookup helpers. PATCH supports clearing owner via explicit `null`. DELETE is 409 if an agent is installed.
- `api/curfew_api/routes/apps.py` — new file, full CRUD. No inbound FKs yet (`target_apps` is deferred), so DELETE is unconditional.
- `api/curfew_api/app.py` — mounts the new routers.
- `api/tests/conftest.py` — extracts the shared `configured_db` / `client` / `auth` fixtures every API integration test was duplicating; `test_locks.py` shrinks ~75 lines.
- 49 new tests (119 total): `test_users_crud.py` (15), `test_devices_crud.py` (19), `test_apps_crud.py` (14), plus 1 added in `test_locks.py`.

**Out (next slice):**
- Agent endpoints (install / heartbeat / state / tokens / manifests).
- Plugin endpoints.
- Settings GET/PATCH and admin snapshot.

## Notable design choices

- **Restrict-by-default DELETE for FKs that protect data.** A user with devices, a device with an agent installed: the route returns 409 with a descriptive detail telling the operator what to clean up first. Cascading those would be too easy to misuse — the operator has to acknowledge the dependent state.
- **Cascade for 1:1 metadata.** `user_lock` is a per-user singleton row that only exists to record "this user is currently manually locked". It's not a separate entity to protect. Deleting the user deletes the lock silently.
- **Schema-level ON DELETE deferred.** Handler-level enforcement is enough for V1 and is easier to reason about per-route. Promote to ON DELETE constraints when the matrix grows.
- **Shared test fixtures in conftest.** Each per-table test file was setting up the same `configured_db`/`client`/`auth` triple. Pulling them into `api/tests/conftest.py` removes ~75 lines from `test_locks.py` and makes new test files trivially short to add.
- **Devices use a translation layer (for now).** API exposes `owner: str | None` (the username) while storage is `owner_id: int`. This was the path of least resistance from the lock-feature slice but caused a PATCH bug (see below). The follow-up slice (PR #13) drops the translation entirely.

## Bugs caught during the slice

- **Device PATCH `owner` setattr crash.** The generic `for field, value in update_data.items(): setattr(row, field, value)` loop tried to set `Device.owner` (the API field), but the model only has `Device.owner_id`. Initially fixed by special-casing `owner`; properly fixed in the follow-up slice (PR #13) by removing the translation entirely.
- **DELETE user with lock — FK ordering violation.** SQLAlchemy doesn't auto-order `session.delete()` calls without a declared `relationship()`, and SQLite's `PRAGMA foreign_keys=ON` catches the violation. Fixed by deleting the `user_lock` first then calling `session.flush()` before the user delete.

## Verification

- `uv sync --all-packages --group dev`, ruff, ruff format, mypy, pre-commit all clean.
- `uv run pytest` — 119 passed (+49 from PR #11's 70).
- Live Docker container exercises the full cascade-conflict path: create user + device, fail user delete with 409, delete device, succeed user delete with 204.

## Branch + PR

- Branch: `crud-completion` off `main`.
- One commit `53aa360`. 11 files changed, 1127 insertions, 56 deletions.
- PR #12, merged.
