# Step 8 — Frontend foundation: scaffold, login, manager dashboard

## Context

ADR-016 (per-user auth) shipped in PR #15 — login + sessions + role gating
all work end-to-end against the API. ADR-017 locked the frontend stack
(Vite + React + TypeScript + Tailwind + shadcn/ui + TanStack Query/Router,
in a new `web/` workspace member, blocked on auth).

This is the first frontend slice. Scope is deliberately tight: scaffold the
project, get **login → manager dashboard with lock/unlock against real data**
working end-to-end, and stop. Admin CRUD pages, settings, and the snapshot
view are each their own follow-up slice.

After this PR an operator can `just web-dev` (or `just docker-run`), point
a browser at the dev server, log in as a real user, see managed users, and
lock/unlock them — entirely from the browser, hitting the live API with
session cookies.

## Scope

**In:**

- New `web/` directory (independent of the uv workspace; uv is Python-only).
- Vite + React + TypeScript (strict) + Tailwind v3 + shadcn/ui + ESLint + Prettier.
- TanStack Query (server state) + TanStack Router (file-based, type-safe).
- React Hook Form + Zod (login form validation).
- `bun` as package manager.
- `openapi-typescript` to generate `web/src/api/schema.ts` from `/v1/openapi.json`. A small `apiFetch()` wrapper sets `credentials: 'include'` and adds typed paths.
- **Login page** (`/login`) — username + password form. POST `/v1/auth/login`. On success: navigate to `/`. On failure: show the 401 error inline.
- **Auth state** — `useMe()` query hits `/v1/auth/me`, cached in TanStack Query. Provides `actor` (or `null`). Loading and 401 states distinguished.
- **Protected route guard** — TanStack Router `beforeLoad` checks the `me` query; redirects to `/login` if 401. Login page redirects to `/` if already authenticated.
- **App layout** — header (app name, current actor's username + role badge, dark-mode toggle, logout), main content area, mobile-responsive (header collapses, no sidebar in slice 1).
- **Manager dashboard** (`/`) — `useUsers()` query hits `GET /v1/users`. For each managed user: row showing username, role, lock status (computed via `GET /v1/users/{user}/status`), and a lock/unlock button. Mutations use TanStack Query's `useMutation` with optimistic-update + rollback on error. Unmanaged users render greyed out with no toggle.
- **Dark mode** — shadcn pattern (CSS variables under `.dark` class on `<html>`, toggle stored in localStorage, respects `prefers-color-scheme` on first load).
- **Vite dev proxy** — `vite.config.ts` proxies `/v1/*` to `http://localhost:8000` so the dev server is same-origin with the API (no CORS in dev).
- **Static-mount in prod** — `api/curfew_api/app.py` adds `app.mount("/", StaticFiles(directory="web/dist", html=True, check_dir=False))` *after* all routers so API paths win. SPA index.html falls through for client-side routes (`html=True` enables SPA fallback). When `web/dist` is missing (developer hasn't built), the mount is a no-op (`check_dir=False`); API keeps working unchanged.
- **`api/Dockerfile`** — adds `COPY web/dist ./web/dist` after the existing copies so the container ships with the SPA already built. Build step runs *before* docker build (developer's `just docker-build` will be updated to first run `bun run build`).
- **Devcontainer** — `.devcontainer/Dockerfile` adds Node 22 + bun. `devcontainer.json` adds Tailwind IntelliSense + ESLint + Prettier extensions. `postCreateCommand` extends to also run `cd web && bun install`.
- **`justfile`** — new recipes: `web-install`, `web-dev`, `web-build`, `web-typecheck`, `web-lint`, `web-format`, `web-check`. Existing `check`, `serve`, and `docker-build` recipes extended to call the web equivalents.
- **CI** — `.github/workflows/ci.yml` gains a parallel `web` job: install, typecheck (`tsc --noEmit`), lint (eslint), build (`vite build`).
- **Tests** — Vitest + React Testing Library + MSW (mock service worker) for API stubbing. Minimal coverage in slice 1: API client wrapper, `useMe` happy/401, login form validation, lock toggle component. ~10 tests; later slices add more.
- **Docs** — `docs/DEVELOPMENT.md` gains a "Frontend" section. `README.md` mentions the new `web/` directory. ADR-017 already in DECISIONS.md from the auth slice.

**Out (later slices):**

- Admin CRUD pages (users / devices / apps / plugins / agents — each its own slice).
- Settings page.
- System snapshot view.
- Sidebar nav (slice 1 has a header only; sidebar lands when admin pages do).
- Toast / notification system (slice 1 uses inline error states + `aria-live` regions).
- Change-password UX (deferred until first user actually needs it).
- E2E tests (Playwright is its own slice).
- i18n.

## Notable design choices

- **`web/` lives outside the uv workspace.** uv only knows about Python members. The repo gains a sibling JS tree with its own `package.json` + `tsconfig.json`. The root `pyproject.toml` is virtual (`package = false`), so there's no conflict.
- **Cookie sessions, not bearer tokens in localStorage.** The auth backend already issues HttpOnly cookies; the SPA never touches the token, which dodges the entire localStorage-XSS-token-theft category. Same-origin (dev: Vite proxy; prod: FastAPI static mount) means no CORS-with-credentials dance.
- **`html=True` + catch-all mount.** The SPA is one `index.html` and TanStack Router does the routing client-side. `StaticFiles(html=True)` serves `index.html` for any path that doesn't match a built file — exactly what an SPA needs. API routes are mounted *before* the catch-all so `/v1/*` keeps working. (FastAPI's router is matched first; the static mount only handles what the routers don't.)
- **`openapi-typescript` over `orval`.** `openapi-typescript` emits a single `schema.ts` of types only — no runtime, no client. Manual `apiFetch()` wrapper does fetching with typed paths. `orval` generates ready-made TanStack Query hooks but adds a heavier abstraction; the simpler thing is fewer surprises and easier to debug. Promote to a generated client when the manual wrapper is annoying — not before.
- **Optimistic updates on lock/unlock.** Toggling a user's lock and waiting for round-trip + status refetch feels sluggish for a one-click action. TanStack Query's optimistic-update with rollback-on-error pattern fits naturally.
- **Header-only chrome in slice 1.** A sidebar implies destinations to navigate to; slice 1 has one screen. The sidebar lands with the admin CRUD slice when there's something to put in it.
- **No `next-themes` dep.** Dark mode is ~30 lines: respect `prefers-color-scheme` on first load, store user override in localStorage, toggle a `dark` class on `<html>`. shadcn's CSS variables handle the rest.
- **Strict TypeScript from day one.** `"strict": true` + `"noUncheckedIndexedAccess": true` in `tsconfig.json`. Mirrors the project's mypy-strict posture.
- **Testing the wrapper, not the implementation.** Component tests assert behaviour (form posts on submit, error rendered on 401) rather than exact DOM shape. Resilient to shadcn version bumps.

## Files to create / touch

| Path | Change |
|---|---|
| `web/package.json` | New: deps + scripts (dev, build, typecheck, lint, format, test). |
| `web/tsconfig.json`, `web/tsconfig.node.json` | New: strict TS config. |
| `web/vite.config.ts` | New: React plugin + dev proxy `/v1` → `http://localhost:8000`. |
| `web/tailwind.config.ts`, `web/postcss.config.js` | New: shadcn-required Tailwind config. |
| `web/index.html` | New: minimal SPA shell. |
| `web/src/main.tsx` | New: app root, sets up QueryClient + Router. |
| `web/src/api/schema.ts` | New (generated): `openapi-typescript`-emitted API types. |
| `web/src/api/client.ts` | New: `apiFetch()` wrapper with `credentials: 'include'` + typed paths. |
| `web/src/auth/useMe.ts` | New: `useMe()` query + `useLogin()` / `useLogout()` mutations. |
| `web/src/components/ui/*` | New: shadcn components (button, input, label, card, badge — only what slice 1 uses). |
| `web/src/components/Layout.tsx` | New: header + main content slot. |
| `web/src/components/DarkModeToggle.tsx` | New: localStorage-backed theme toggle. |
| `web/src/routes/__root.tsx` | New: TanStack Router root with auth-aware redirect logic. |
| `web/src/routes/login.tsx` | New: login page. |
| `web/src/routes/index.tsx` | New: manager dashboard. |
| `web/src/routes/users/$user.lock.tsx` (or component) | The lock toggle is a component used by the index, not its own route. |
| `web/.eslintrc.cjs`, `web/.prettierrc.json` | New: lint/format config. |
| `web/vitest.config.ts`, `web/src/setupTests.ts`, `web/src/**/__tests__/*` | New: ~10 tests. |
| `web/.gitignore` | New: `node_modules`, `dist`, `.vite`, `coverage`. |
| `api/curfew_api/app.py` | Add `StaticFiles` mount after routers. |
| `api/Dockerfile` | `COPY web/dist ./web/dist` after Python copies. |
| `.devcontainer/Dockerfile` | Install Node 22 + bun. |
| `.devcontainer/devcontainer.json` | Add JS extensions, extend `postCreateCommand`. |
| `.github/workflows/ci.yml` | Add parallel `web` job. |
| `justfile` | Add `web-*` recipes; extend `check` and `docker-build` to include them. |
| `docs/DEVELOPMENT.md` | New "Frontend" section. |
| `README.md` | One-line mention of `web/`. |

## Reused utilities / existing surface

- `GET /v1/auth/me`, `POST /v1/auth/login`, `POST /v1/auth/logout` (PR #15).
- `GET /v1/users` (existing CRUD list).
- `GET /v1/users/{user}/status` (existing rule pipeline).
- `POST /v1/users/{user}/lock` and `/unlock` (PR #11).
- `core/curfew/config.py:cors_origins` — already wired; not used in slice 1 because Vite proxy + static mount keep everything same-origin. Documented as the escape hatch for hosting the SPA on a different origin later.
- `/v1/openapi.json` — input to `openapi-typescript`.

## Verification

- **Local dev (two terminals):**
  - Terminal 1: `just serve` (FastAPI on :8000).
  - Terminal 2: `just web-dev` (Vite on :5173).
  - Browser: open http://localhost:5173, get redirected to `/login`.
  - Bootstrap: `curl -X POST -H 'Authorization: Bearer dev-token-not-secure' -H 'Content-Type: application/json' -d '{"username":"alice","password":"test1234","role":"admin"}' http://localhost:8000/v1/users` (or `just docker-run` and use the docker token).
  - Login as alice → land on `/` → see kid users → lock/unlock works → row updates immediately.
  - Logout → back to `/login`.
  - Dark mode toggle persists across reload.
- **CI:** Python lane stays green; new `web` lane runs typecheck + eslint + build + vitest, all green.
- **`just check`:** runs both Python and web checks (extended).
- **Production container:** `bun run --cwd web build && just docker-build && just docker-run`. Same flow against http://localhost:8000 (port 9876 in justfile). FastAPI serves `index.html` at `/`; client-side routing works on hard reloads of `/login`, `/`.
- **Build size sanity:** Vite production bundle target < ~250 KB gzipped for slice 1's screens. (Soft target; CI doesn't enforce.)

## Branch + PR

- Branch: `frontend-foundation` off `main`.
- One PR. Estimated diff: ~1,500 lines added across ~40 new files (most of it new — generated schema, shadcn components, config). Most of the surface is *new*, not edits to existing code.
