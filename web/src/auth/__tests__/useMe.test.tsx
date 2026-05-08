import { describe, expect, it } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";
import { ME_QUERY_KEY, useLogin, useLogout, useMe } from "@/auth/useMe";
import { http, HttpResponse, server } from "@/test-utils/server";

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("useMe", () => {
  it("returns the actor when logged in", async () => {
    server.use(
      http.get("/v1/auth/me", () =>
        HttpResponse.json({ kind: "user", username: "alice", role: "admin" }),
      ),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => useMe(), { wrapper: wrapper(client) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({
      kind: "user",
      username: "alice",
      role: "admin",
    });
  });

  it("returns null on 401", async () => {
    // server's default handler is 401.
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => useMe(), { wrapper: wrapper(client) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });
});

describe("useLogin", () => {
  it("plants the fresh /me actor in the cache so route guards see it", async () => {
    // Stage 1: cache holds the pre-login null from the page's first /me probe.
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    client.setQueryData(ME_QUERY_KEY, null);

    // Stage 2: server now answers /me as alice (the cookie was set by the
    // login response we're stubbing below).
    server.use(
      http.post("/v1/auth/login", () => new HttpResponse(null, { status: 204 })),
      http.get("/v1/auth/me", () =>
        HttpResponse.json({ kind: "user", username: "alice", role: "admin" }),
      ),
    );

    const { result } = renderHook(() => useLogin(), { wrapper: wrapper(client) });
    result.current.mutate({ username: "alice", password: "test1234" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // Stage 3: the cache must hold the new actor synchronously after the
    // mutation settles — `ensureQueryData` (used by route beforeLoad) reads
    // the cache without refetching, so anything else here would loop the
    // user back to /login.
    await waitFor(() =>
      expect(client.getQueryData(ME_QUERY_KEY)).toEqual({
        kind: "user",
        username: "alice",
        role: "admin",
      }),
    );
  });
});

describe("useLogout", () => {
  it("clears the cached actor on success so the login guard takes over", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    client.setQueryData(ME_QUERY_KEY, {
      kind: "user",
      username: "alice",
      role: "admin",
    });

    server.use(http.post("/v1/auth/logout", () => new HttpResponse(null, { status: 204 })));

    const { result } = renderHook(() => useLogout(), { wrapper: wrapper(client) });
    result.current.mutate();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(client.getQueryData(ME_QUERY_KEY)).toBeNull();
  });
});
