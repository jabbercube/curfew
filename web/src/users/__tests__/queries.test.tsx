import { describe, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { useCreateUser, useDeleteUser } from "@/users/queries";
import { http, HttpResponse, server } from "@/test-utils/server";

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("useCreateUser", () => {
  it("posts the payload to /v1/users and returns the created user", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/v1/users", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          { id: 1, username: "alice", role: "admin", managed: true },
          { status: 201 },
        );
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const { result } = renderHook(() => useCreateUser(), { wrapper: wrapper(client) });
    result.current.mutate({
      username: "alice",
      password: "test1234",
      role: "admin",
      managed: true,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured).toEqual({
      username: "alice",
      password: "test1234",
      role: "admin",
      managed: true,
    });
    expect(result.current.data).toEqual({
      id: 1,
      username: "alice",
      role: "admin",
      managed: true,
    });
  });
});

describe("useDeleteUser", () => {
  it("calls DELETE /v1/users/{username}", async () => {
    let calledPath: string | null = null;
    server.use(
      http.delete("/v1/users/:user", ({ request }) => {
        calledPath = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const { result } = renderHook(() => useDeleteUser(), { wrapper: wrapper(client) });
    result.current.mutate("alice");
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calledPath).toBe("/v1/users/alice");
  });
});
