# Step 7 — Per-user authentication (minimal: username + password)

## Context

The frontend slice is blocked on this. Today the API only accepts the operator
root token (`CURFEW_ROOT_TOKEN`) as bearer auth, which is fine for CLI/ops but
unworkable for a multi-user web UI where managers and admins log in with
distinct identities. The frontend needs:

- a real login endpoint that accepts username + password,
- a session mechanism it can rely on (cookie-based, so the SPA doesn't manage tokens),
- per-user role enforcement (manager can lock/unlock; admin can do everything),
- an introspection endpoint (`/auth/me`) for nav state.

**Explicitly deferred** (per scope decision, see prior conversation):
- Email column on `users` and login-by-username-or-email.
- Google OAuth (or any third-party identity).
- Login throttling / lockout.
- Password reset flow (no email infrastructure yet).
- CLI login UX — CLI keeps using root token for now.

The root token continues to work alongside per-user auth so bootstrap, ops,
and the still-skeleton CLI aren't broken.

## Scope

**In:**

- New SQLModel `Session` (table `sessions`): `id` (random opaque token, PK), `user_id` (FK), `created_at`, `expires_at`, `last_seen_at`. Indexed on `user_id` and `expires_at`.
- `users.password_hash` column (nullable: NULL means "no password set; cannot log in" — used for the root token's synthetic actor and for users created by the bootstrap path).
- Argon2 password hashing via `argon2-cffi`. New password verification + hashing helpers in `core/curfew/passwords.py`.
- New Alembic migration `0002_per_user_auth.py`: adds `users.password_hash`, creates `sessions` table.
- New value object `Actor` replacing the bare `str` actor: `kind: Literal["root", "user"]`, `username: str | None`, `role: UserRole`, `user_id: int | None`. Root actor synthesizes `role=ADMIN`.
- Rewritten `api/curfew_api/auth.py`: `require_actor` checks bearer (root token) → cookie session → 401. `require_role(min_role)` factory returns a dep that 403s on insufficient role.
- New router `api/curfew_api/routes/auth.py`:
  - `POST /v1/auth/login` — `{username, password}` → 200 + `Set-Cookie: curfew_session=...` (HttpOnly, Secure-when-https, SameSite=Lax). Audited as `auth.login`.
  - `POST /v1/auth/logout` — clears cookie + deletes session row. Audited as `auth.logout`.
  - `GET /v1/auth/me` — returns `{username, role, kind}` for the current actor.
  - `POST /v1/auth/change-password` — `{current_password, new_password}` → 204. Audited.
- `core/curfew/schemas.py` additions: `LoginRequest`, `MeResponse`, `ChangePasswordRequest`. `UserCreate` gains required `password: str` (length validation: `Field(min_length=8)`).
- `core/curfew/audit.py` — `record_audit` accepts `Actor` (or its `audit_str`) instead of bare `str`. Backward-compatible: callers that pass `Actor` get `actor.audit_str` written to the row.
- Role-gating applied to existing routes:
  - User/device/app **writes** (`POST`/`PATCH`/`DELETE`) → admin only.
  - Lock/unlock (`POST /v1/users/{user}/lock` and `/unlock`) → manager or admin.
  - Settings (`GET`/`PATCH`) → admin only.
  - System snapshot → admin only.
  - Reads (`GET /v1/users`, `GET /v1/users/{user}`, status, list-apps, list-devices) → any authenticated.
  - Health remains unauthenticated.
- Tests:
  - `core/tests/test_passwords.py` — hash + verify roundtrip, wrong password rejected, hash format.
  - `api/tests/test_auth.py` — login success / wrong password / unknown user / no body / no password set; logout clears cookie + deletes session; `/auth/me` for root, user, unauthed; change-password success / wrong-current / weak.
  - `api/tests/test_role_gating.py` — admin can do everything; manager can lock/unlock but not user CRUD; member is rejected from writes; root token still works against everything.
  - Existing tests updated where they post to `/v1/users` (must now include `password` in the body). The conftest `auth` fixture (root token) keeps working unchanged.

**Out (later slices):**

- Email column + login-by-either + email-driven flows.
- Google OAuth.
- Login throttling, account lockout, audit-of-failed-login.
- CLI login UX (keeps using root token).
- Frontend itself (the next slice — unblocked by this one).
- Password complexity beyond `min_length=8`.

## Notable design choices

- **Server-side sessions in a `sessions` table** rather than JWTs. Simpler invalidation (`DELETE FROM sessions WHERE id = ?`), no key-rotation story, no denylist. The DB hit per request is negligible at homelab scale and is a single index lookup. Cookie holds an opaque random token; nothing useful to steal beyond the session itself.
- **`argon2-cffi` directly, not `passlib`.** Passlib's release cadence has stalled; argon2-cffi is actively maintained and gives us exactly the API we need (`PasswordHasher().hash() / .verify()`). If algorithm migration becomes a real concern later, we can add passlib then; we're not paying that complexity tax up front.
- **Plain cookie handling, no `SessionMiddleware`.** Starlette's `SessionMiddleware` is for *signed cookies that hold session data*; we want *opaque cookies that hold a session id*. The dep reads `request.cookies["curfew_session"]` and looks the row up. No `itsdangerous` dependency.
- **`Actor` value object, not a bare `str`.** The audit log gets `actor.audit_str` (`"operator"` for root, the username for users) so existing audit history stays comparable. Routes that need to gate on role read `actor.role`; routes that don't, just consume the dep without unpacking.
- **Root token synthesizes `role=ADMIN`.** A request authenticated via root bearer behaves as an admin actor — same code path through the role-gating deps, no special-casing in route bodies. Audit string stays `"operator"` so the existing audit history doesn't fork.
- **`users.password_hash` nullable, not required.** A NULL hash means "this user can't log in via password" — covers users created before the migration and any future SSO-only users. The login route returns 401 (not a different code) so we don't leak which usernames are password-capable.
- **Sliding sessions, 7-day lifetime, hardcoded.** Refresh `last_seen_at` and bump `expires_at` on every authenticated request. Configurable lifetime is a future feature; nothing in V1 needs it tunable.
- **Bootstrap path is "use root token to create the first admin user."** No migration-time admin seeding, no `CURFEW_BOOTSTRAP_PASSWORD` env. The operator boots with the root token, `POST /v1/users {username, password, role: "admin"}`, then logs in as that user via the frontend. Root token stays usable as a recovery path.
- **Login route does not auto-create a session for the root token.** The root token is a stateless bearer; it doesn't need a session row. If the operator wants a session, they create + log in as a user.

## Files to touch

| File | Change |
|---|---|
| `core/curfew/models.py` | Add `password_hash: str \| None` to `User`. Add new `Session` SQLModel class (table `sessions`). |
| `core/curfew/passwords.py` | New: `hash_password(plain) -> str`, `verify_password(plain, hash) -> bool`. argon2-cffi-backed. |
| `core/curfew/auth.py` | New: `Actor` dataclass + factory helpers. (Currently the `auth` notion lives only in `api/`; pulling the value object into `core/` keeps it importable by audit + future SDK.) |
| `core/curfew/audit.py` | `record_audit` accepts `Actor \| str` (str preserved for compat); writes `actor.audit_str` when given an Actor. |
| `core/curfew/schemas.py` | Add `LoginRequest`, `MeResponse`, `ChangePasswordRequest`. `UserCreate` gains `password: str = Field(min_length=8)`. |
| `core/curfew/__init__.py` | Re-export `Actor`, `Session`, password helpers, new schemas. |
| `core/pyproject.toml` | Add `argon2-cffi>=23.1`. |
| `api/curfew_api/auth.py` | Replace `require_operator` with `require_actor` (root-or-session) + `require_role(min)` factory. Keep `Operator` alias for "any authenticated" so existing routes need only minimal touch. |
| `api/curfew_api/routes/auth.py` | New: login, logout, me, change-password. |
| `api/curfew_api/routes/users.py` | Hash password on create. Apply admin-only gating to POST/PATCH/DELETE. |
| `api/curfew_api/routes/devices.py` | Apply admin-only gating to POST/PATCH/DELETE. |
| `api/curfew_api/routes/apps.py` | Apply admin-only gating to POST/PATCH/DELETE. |
| `api/curfew_api/routes/locks.py` | Apply manager+ gating. |
| `api/curfew_api/routes/settings.py` | Apply admin-only gating. |
| `api/curfew_api/routes/system.py` | Apply admin-only gating. |
| `api/curfew_api/app.py` | Mount `auth` router. |
| `api/migrations/versions/0002_per_user_auth.py` | New migration: add `users.password_hash`; create `sessions` table with two indexes. |
| `api/tests/conftest.py` | Add `admin_user` / `manager_user` / `member_user` fixtures that create + log in test users and yield cookie-bearing clients. |
| `api/tests/test_auth.py` | New, ~12 tests covering login/logout/me/change-password and edge cases. |
| `api/tests/test_role_gating.py` | New, ~10 tests covering role-tiered access on writes. |
| `api/tests/test_users_crud.py` etc. | Add `password` field to `client.post("/v1/users", ...)` calls. |
| `core/tests/test_passwords.py` | New, ~4 tests on the hashing helpers. |
| `docs/PLAN.md` | Update §"Authentication" to reflect implemented per-user auth (replaces the "shaped to accept it" wording). Add `/v1/auth/*` to the API surface table. |
| `docs/DECISIONS.md` | Add **ADR-016** (per-user auth approach) and **ADR-017** (frontend stack + audience). See "ADRs to land in this PR" below. |

## ADRs to land in this PR

**ADR-016 — Per-user authentication: username + password with server-side sessions.**
*Decided:* per-user auth uses a username + Argon2 password hash on `users`, opaque random session ids stored in a new `sessions` table, and HttpOnly + SameSite=Lax cookies. Roles already on `User.role` (member / manager / admin) drive a `require_role(min)` dep factory. The operator root token continues to work alongside per-user auth as a synthesised admin actor (kind=root, audit-string="operator").
*Rejected:* JWTs (invalidation/rotation complexity for no homelab-scale benefit); passlib (stalled release cadence — argon2-cffi directly is cleaner); Starlette `SessionMiddleware` (we want opaque session ids in cookies, not signed cookies that hold session data); seeding an admin user via migration (one-way trap if password lost — root token recovery is the better escape hatch); login-by-username-or-email (deferred with the email column itself); Google OAuth (deferred — single-user homelab admin doesn't justify the OAuth surface); login throttling (deferred to V2).
*Consequences:* root-token bearer remains the bootstrap and recovery path; CLI keeps using the root token until a future session-aware CLI slice; `users.password_hash` is nullable so future SSO-only users (or root-token-equivalents) don't have to fake a password; the audit log stays comparable across pre- and post-auth history because root-token writes still record actor `"operator"`.

**ADR-017 — Frontend stack: Vite + React + TypeScript + shadcn, operator audience only.**
*Decided:* the operator UI lives in a new `web/` workspace member, built with Vite + React + TypeScript + Tailwind + shadcn/ui + TanStack Query + TanStack Router. Static export (no SSR runtime). Audience is admins and managers only; members get no UI. API types are generated from `/v1/openapi.json` so request/response shapes can't drift. Cookie-based session auth (same-origin in prod; dev-server proxy to the API). Frontend work is blocked until ADR-016 ships.
*Rejected:* Next.js (no SSR / SEO need, and the app-router conventions are more surface than a homelab admin justifies); HTMX + Jinja2 inside the FastAPI process (genuinely a strong fit for this shape but rejected because the operator wants to learn modern React/TanStack tooling and the JSON-API + multi-client trajectory is already established); SPA framework alternatives (Next.js / SvelteKit / Solid — none win against a learning-React goal); a "throwaway Figma-style mockup" first pass (the listed scope — auth pages, dashboard layout, API client — already implies a real working prototype, not paper).
*Consequences:* the repo gains a JS/TS toolchain alongside the Python workspace (Node + a package manager — likely bun or pnpm); deployment is static files (served by FastAPI itself or any static host); component-level role gating in the UI is presentation only — server-side enforcement (ADR-016) is what actually protects the API; first frontend slice will scaffold + login flow + manager dashboard; admin CRUD pages follow as separate slices.

## Reused utilities

- `core/curfew/audit.py:record_audit` — extended to accept `Actor`; existing `actor=str` calls keep working.
- `core/curfew/models.py:UserRole` (MEMBER/MANAGER/ADMIN) — already exists, drives the role-gate factory.
- `core/curfew/db.py:get_session` — unchanged, used by the new auth route.
- `api/tests/conftest.py:configured_db` / `client` / `auth` — root-token fixtures stay; new role-typed user fixtures layer on top.
- `secrets.token_urlsafe(32)` (stdlib) — session id generator.
- `secrets.compare_digest` (already used by `require_operator`) — preserved for the root-token check.

## Verification

- `uv sync --all-packages --group dev` picks up `argon2-cffi`.
- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pre-commit run --all-files` — all clean.
- `uv run pytest` — current 138 → ~170 (estimated +32 across new test files; some existing tests gain a `password` field but no count change).
- Live Docker:
  - `just docker-build && docker run -e CURFEW_ROOT_TOKEN=secret -p 9876:8000 curfew-core:dev`
  - Root token still works: `curl -H 'Authorization: Bearer secret' localhost:9876/v1/users` → 200.
  - Create a real admin user: `curl -X POST -H 'Authorization: Bearer secret' -H 'Content-Type: application/json' -d '{"username":"alice","password":"correct horse","role":"admin"}' localhost:9876/v1/users` → 201.
  - Login as alice: `curl -c jar -X POST -H 'Content-Type: application/json' -d '{"username":"alice","password":"correct horse"}' localhost:9876/v1/auth/login` → 200 + `curfew_session` cookie.
  - `curl -b jar localhost:9876/v1/auth/me` → `{"username":"alice","role":"admin","kind":"user"}`.
  - Logout: `curl -b jar -X POST localhost:9876/v1/auth/logout` → 204; subsequent `/auth/me` → 401.
  - Negative: wrong password → 401; member user attempting `POST /v1/users` → 403; manager user attempting `/v1/users/{u}/lock` → 200; manager attempting `DELETE /v1/users/{u}` → 403.

## Branch + PR

- Branch: `per-user-auth` off `main`.
- One commit, one PR. Estimated diff: ~700 lines added across ~20 files (most of the surface is a single new router + a single new migration; the rest is ~3-line edits per existing route to swap the dep).
