// Admin route guard. Each admin-only route's `beforeLoad` calls this to
// resolve the current actor and redirect non-admins back to the dashboard.
//
// Belt-and-suspenders: the API still 403s for non-admin writes (per ADR-016),
// so this is purely UX — hiding pages a manager can't act on.

import { redirect } from "@tanstack/react-router";
import type { QueryClient } from "@tanstack/react-query";
import { meQueryOptions } from "@/auth/useMe";

export async function requireAdmin(context: { queryClient: QueryClient }) {
  const me = await context.queryClient.ensureQueryData(meQueryOptions);
  if (!me) {
    throw redirect({ to: "/login" });
  }
  if (me.role !== "admin") {
    throw redirect({ to: "/" });
  }
}

export async function requireAuthenticated(context: { queryClient: QueryClient }) {
  const me = await context.queryClient.ensureQueryData(meQueryOptions);
  if (!me) {
    throw redirect({ to: "/login" });
  }
}
