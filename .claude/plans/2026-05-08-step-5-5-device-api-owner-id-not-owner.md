# Step 5.5 — Device API: owner_id, not owner

## Context

Step 5 (PR #12) shipped device CRUD with a translation layer: API took `owner: str | None` (username), storage was `owner_id: int`, and two `_resolve_*` helpers + a `_to_read` serializer bridged them. That came from the lock-feature slice's habit of "URL paths use slugs/usernames" and bled into request/response bodies without anyone questioning it.

The user did question it: "Why not just use `owner_id` in the api?" — and the answer was: no good reason. *Read responses already exposed `id`, so hiding `owner` specifically was a half-measure. Drop the translation, expose `owner_id` directly, and the routes simplify, the PATCH bug class becomes unrepresentable, and reads stop N+1'ing a User lookup per device.

This PR also crystallizes a principle for the rest of the project: **API shape = DB storage shape for true FK columns.** `id` is the canonical FK reference; the human handle (slug, username) is the URL/display reference.

## Scope

**In:**
- `core/curfew/schemas.py` — `DeviceCreate` / `DeviceRead` / `DeviceUpdate` switch `owner: str | None` → `owner_id: int | None`. `DeviceRead` gets `ConfigDict(from_attributes=True)` so FastAPI can serialize a `Device` ORM row directly.
- `api/curfew_api/routes/devices.py` — drops `_resolve_owner_username`, `_resolve_owner_id`, and `_to_read`. Replaced with a single `_validate_owner_id` pre-check that returns a clean 404 with the offending id ("owner_id 999 not found") instead of an opaque post-INSERT `IntegrityError`. The generic `setattr` loop in `update_device` now works without special-casing — `owner_id` is a real model attribute.
- `api/tests/test_devices_crud.py` — every test that previously sent `owner: "kid1"` now does the create_user dance to capture the id, then sends `owner_id`.
- `api/tests/test_users_crud.py` — `delete_user_with_devices_409` similarly updated.

**Out (intentionally NOT in scope):**
- `Plugin.users` (the JSON list of user handles on plugins). It's a JSON array of strings, includes a `"*"` wildcard for "all managed users", and is not a real FK column. The "shape = storage" principle keeps it as user handles. Asked and answered during the design discussion.

## Notable design choices

- **API shape = DB storage shape for true FK columns.** New principle, applies going forward to any route that exposes a foreign-key field. URL path stays human (`/v1/devices/rig`), payload `owner_id` is the int.
- **Pre-check FK existence rather than catching `IntegrityError`.** Catching the post-INSERT FK violation would also work but yields an opaque error. `_validate_owner_id` runs before the INSERT and returns a clean 404 with the specific offending id.
- **`from_attributes=True` instead of a hand-rolled serializer.** `DeviceRead` can now serialize a `Device` ORM row directly. `_to_read` deleted.

## Bug class eliminated

The PR #12 PATCH bug — `for field, value in update_data.items(): setattr(row, field, value)` crashing because `Device.owner` doesn't exist on the model — becomes unrepresentable. There's no `owner` attribute being `setattr`'d anymore; only `owner_id`, which is real.

## Verification

- ruff / ruff format / mypy / pre-commit all clean.
- `uv run pytest` — 119 passing (same count; tests reshaped, not expanded).
- Live Docker container exercises POST/GET/list of devices with `owner_id` and 404-on-bad-id.

## Branch + PR

- Branch: `device-owner-id` off `main`.
- One commit `c27b02e`. 4 files changed, 79 insertions, 89 deletions (net -10).
- PR #13, merged.
