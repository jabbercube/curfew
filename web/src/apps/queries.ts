// App-catalog CRUD query hooks. Mirrors src/users/queries.ts.

import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { components } from "@/api/schema";

export type App = components["schemas"]["AppRead"];
export type AppCreate = components["schemas"]["AppCreate"];

export const appsQueryOptions = queryOptions({
  queryKey: ["apps"] as const,
  queryFn: ({ signal }) => apiFetch<App[]>("/v1/apps", { signal }),
});

export function useCreateApp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AppCreate) =>
      apiFetch<App>("/v1/apps", { method: "POST", body: payload }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["apps"] }),
  });
}

export function useDeleteApp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) =>
      apiFetch<void>(`/v1/apps/${encodeURIComponent(slug)}`, {
        method: "DELETE",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["apps"] }),
  });
}
