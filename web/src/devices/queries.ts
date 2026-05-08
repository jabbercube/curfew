// Device CRUD query hooks. Mirrors src/users/queries.ts in shape; future
// list/detail/edit hooks land here too.

import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { components } from "@/api/schema";

export type Device = components["schemas"]["DeviceRead"];
export type DeviceCreate = components["schemas"]["DeviceCreate"];

export const devicesQueryOptions = queryOptions({
  queryKey: ["devices"] as const,
  queryFn: ({ signal }) => apiFetch<Device[]>("/v1/devices", { signal }),
});

export function useCreateDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: DeviceCreate) =>
      apiFetch<Device>("/v1/devices", { method: "POST", body: payload }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["devices"] }),
  });
}

export function useDeleteDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) =>
      apiFetch<void>(`/v1/devices/${encodeURIComponent(slug)}`, {
        method: "DELETE",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["devices"] }),
  });
}
