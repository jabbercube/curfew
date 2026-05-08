# Step 9 — Admin CRUD UI: users / devices / apps

## Context

PR #16 shipped the frontend foundation (login + manager dashboard with
lock/unlock). The operator can sign in and lock/unlock managed users from
the browser, but every other write — creating users, managing devices,
managing apps — still requires `curl` or `just user-create`.

This slice closes the operator self-service loop for admins: list, create,
and delete users / devices / apps from the UI. After this PR an admin
never needs the CLI for routine data management.

## Scope

**In:**

- New shadcn primitives (`web/src/components/ui/`): `dialog`, `select`,
  `separator`, `table`. Adds Radix UI peers as deps.
- `sonner` (toast notifications) — mounted once at app root, called from
  mutations to surface success / error feedback.
- New `<Sidebar>` + `<AdminLayout>` chrome — replaces the header-only
  layout for protected pages. Mobile: collapses to top-nav. Sidebar entries
  filtered by role (managers see Dashboard only; admins see everything).
- New routes (file-based, all under admin guard):
  - `/users` — list + Create dialog + Delete confirm
  - `/devices` — list + Create dialog + Delete confirm
  - `/apps` — list + Create dialog + Delete confirm
- **Admin route guard** — TanStack Router `beforeLoad` on each admin route
  reads `me` from the cache and `throw redirect({ to: "/" })` for
  non-admin actors. Belt-and-suspenders: the API still 403s for them.
- Reusable `<DeleteConfirmDialog>` component (one shape across all three
  pages).
- Tests: admin guard redirects manager away from `/users`; users-page
  create posts correct body; delete confirm calls DELETE.

**Out (next slice):**

- PATCH (edit) flows. Read + Create + Delete validates the pattern; edit
  is the heaviest because each resource has different mutable surfaces.
- Settings + system snapshot pages (different shape: singleton tunables /
  big read-only blob).
- Plugins + agents pages (their CRUD doesn't exist on the backend yet).
- Per-resource detail pages with tabs / nested data.
- Pagination — three resources at homelab scale stay short.

## Notable design choices

- **List + Create + Delete only.** PATCH is the slice's natural cliff:
  edit forms differ per resource (devices have list-of-MACs, apps have
  three list fields, users have role + managed toggles). Doing them all
  here would double the diff for marginal user value. Better to ship the
  CRUD shell now and iterate.
- **Sidebar over kept header.** Three new pages need destinations; a
  sidebar is the natural shape. The dashboard stays at `/` and gets a nav
  entry.
- **`sonner` for toasts.** shadcn's recommended toast lib (Radix-free,
  small, accessible). Calling `toast.success(...)` from `useMutation`
  callbacks keeps mutation feedback co-located with the call site.
- **Role-aware nav, not just role-gated routes.** Manager-role users see
  only "Dashboard" in the sidebar; admin-role sees everything. Hidden
  links don't 403 — the backend still does — but the UX is cleaner.
- **No global error boundary in this slice.** TanStack Query's
  `mutation.onError` already surfaces failures via toasts; route-level
  `errorComponent` would be premature without a real error case to design
  around. Add when V2 needs it.
- **`owner_id: number | null` rendered as a `<Select>` of usernames.** The
  API takes `owner_id` (int) — ADR-013 ("API shape = DB storage shape" for
  FKs) — but humans pick by username. Resolution happens at the form
  layer.

## Files to touch / create

| Path | Change |
|---|---|
| `web/package.json` | Add `sonner`, `@radix-ui/react-dialog`, `@radix-ui/react-select`, `@radix-ui/react-separator`. |
| `web/src/components/ui/dialog.tsx` | New shadcn primitive. |
| `web/src/components/ui/select.tsx` | New shadcn primitive. |
| `web/src/components/ui/separator.tsx` | New shadcn primitive. |
| `web/src/components/ui/table.tsx` | New shadcn primitive. |
| `web/src/components/ui/sonner.tsx` | New shadcn `<Toaster>` wrapper. |
| `web/src/components/Sidebar.tsx` | New: nav links filtered by role. |
| `web/src/components/AdminLayout.tsx` | New: sidebar + content for protected routes. Replaces inline `<Layout>` use in routes. |
| `web/src/components/DeleteConfirmDialog.tsx` | New: reusable confirm dialog. |
| `web/src/users/queries.ts` | Add `useCreateUser`, `useDeleteUser`. |
| `web/src/devices/queries.ts` | New: `useDevices`, `useCreateDevice`, `useDeleteDevice`. |
| `web/src/apps/queries.ts` | New: `useApps`, `useCreateApp`, `useDeleteApp`. |
| `web/src/routes/users.tsx` | New route: admin-only, list + create + delete. |
| `web/src/routes/devices.tsx` | New route: admin-only, list + create + delete. |
| `web/src/routes/apps.tsx` | New route: admin-only, list + create + delete. |
| `web/src/routes/index.tsx` | Switch to `<AdminLayout>` so the sidebar shows on the dashboard too. |
| `web/src/main.tsx` | Mount `<Toaster>` once at the app root. |
| `web/src/auth/admin-guard.ts` | New: small helper that route `beforeLoad` calls to enforce admin. |
| `web/src/**/__tests__/*` | New tests: admin guard, users-page create, delete confirm. |

## Reused

- `useMe` / `useLogin` / `useLogout` from PR #16.
- `apiFetch` from PR #16.
- TanStack Router `beforeLoad` pattern from `routes/login.tsx` /
  `routes/index.tsx`.
- Shadcn `<Card>`, `<Button>`, `<Input>`, `<Label>`, `<Badge>` already
  present.

## Verification

- `bun run typecheck`, `bun run lint`, `bun run format:check`,
  `bun run test`, `bun run build` — all clean.
- `uv run pytest` (no Python regressions expected — slice is web-only
  beyond the mount point).
- Live local:
  - `just serve` + `just web-dev`.
  - Sign in as admin (alice).
  - Sidebar shows Dashboard / Users / Devices / Apps.
  - `/users`: see existing users; create kid via dialog → list updates →
    success toast; delete kid → confirm → list updates → toast.
  - Same for `/devices` and `/apps`.
  - Sign out, sign in as a manager-role user → sidebar shows Dashboard
    only; navigating to `/users` redirects to `/`.
- Live Docker: `just docker-build && just docker-run`. Same flow against
  http://localhost:8000.

## Branch + PR

- Branch: `admin-crud-ui` off `main` (already created).
- One PR. Estimated diff: ~1,200 lines added across ~15 new files.
