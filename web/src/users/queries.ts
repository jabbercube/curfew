// User + lock-status query hooks. Kept colocated under src/users so future
// admin CRUD pages have an obvious home for the rest of the user surface.

import { queryOptions, useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { components } from "@/api/schema";

export type User = components["schemas"]["UserRead"];
export type LockStatus = components["schemas"]["LockStatus"];

export const usersQueryOptions = queryOptions({
  queryKey: ["users"] as const,
  queryFn: ({ signal }) => apiFetch<User[]>("/v1/users", { signal }),
});

export const userStatusQueryKey = (username: string) => ["users", username, "status"] as const;

export const userStatusQueryOptions = (username: string) =>
  queryOptions({
    queryKey: userStatusQueryKey(username),
    queryFn: ({ signal }) =>
      apiFetch<LockStatus>(`/v1/users/${encodeURIComponent(username)}/status`, { signal }),
  });

/** Run one status query per user. Empty list -> no fetches. */
export function useUserStatuses(usernames: string[]) {
  return useQueries({
    queries: usernames.map((u) => userStatusQueryOptions(u)),
  });
}

export function useToggleLock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ username, locked }: { username: string; locked: boolean }) =>
      apiFetch<LockStatus>(
        `/v1/users/${encodeURIComponent(username)}/${locked ? "lock" : "unlock"}`,
        { method: "POST" },
      ),
    // Optimistic update: flip the cached LockStatus immediately so the row
    // re-renders on click. Roll back on error; refetch on settled to reconcile
    // with the server (which may add or remove additional reasons).
    onMutate: async ({ username, locked }) => {
      const key = userStatusQueryKey(username);
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<LockStatus>(key);
      qc.setQueryData<LockStatus>(key, (old) => ({
        locked,
        reasons: locked
          ? [...(old?.reasons ?? []), { kind: "manual_lock" }]
          : (old?.reasons ?? []).filter((r) => r.kind !== "manual_lock"),
      }));
      return { previous };
    },
    onError: (_err, { username }, ctx) => {
      if (ctx?.previous) {
        qc.setQueryData(userStatusQueryKey(username), ctx.previous);
      }
    },
    onSettled: (_data, _err, { username }) => {
      qc.invalidateQueries({ queryKey: userStatusQueryKey(username) });
    },
  });
}
