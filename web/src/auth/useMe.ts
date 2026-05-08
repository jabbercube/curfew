// Auth state hooks built on TanStack Query.
//
// `meQueryOptions` is exported so route loaders (TanStack Router `beforeLoad`)
// can run the same query through `queryClient.ensureQueryData(...)` to gate
// access. The mutations invalidate the cached `me` so subsequent renders see
// the new state without an extra fetch round trip.

import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, ApiError } from "@/api/client";
import type { components } from "@/api/schema";

export type Me = components["schemas"]["MeResponse"];
export type LoginRequest = components["schemas"]["LoginRequest"];

export const ME_QUERY_KEY = ["me"] as const;

/** Fetch /v1/auth/me; returns null for an unauthenticated 401. */
async function fetchMe(signal?: AbortSignal): Promise<Me | null> {
  try {
    return await apiFetch<Me>("/v1/auth/me", { signal });
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      return null;
    }
    throw err;
  }
}

export const meQueryOptions = queryOptions({
  queryKey: ME_QUERY_KEY,
  queryFn: ({ signal }) => fetchMe(signal),
  // The actor doesn't change without our action — refetching on every focus
  // adds noise. Mutations invalidate explicitly.
  staleTime: Infinity,
  retry: false,
});

export function useMe() {
  return useQuery(meQueryOptions);
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: LoginRequest) =>
      apiFetch<void>("/v1/auth/login", { method: "POST", body: payload }),
    // We have to physically replace the cached actor because the route
    // `beforeLoad` guards use `ensureQueryData`, which returns cached data
    // regardless of stale flag. `staleTime: Infinity` on meQueryOptions also
    // makes `fetchQuery` short-circuit. So we clear the cache, then refetch
    // — that's what plants the new actor synchronously before navigate fires.
    onSuccess: async () => {
      qc.removeQueries({ queryKey: ME_QUERY_KEY });
      await qc.fetchQuery(meQueryOptions);
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<void>("/v1/auth/logout", { method: "POST" }),
    // Same reason as useLogin: route guards read the cache synchronously, so
    // we have to plant the new value (null = unauthenticated) directly. A
    // refetch would also work but adds a request the server already told us
    // the answer to.
    onSettled: () => {
      qc.setQueryData<Me | null>(ME_QUERY_KEY, null);
    },
  });
}
