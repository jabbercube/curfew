import { describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { requireAdmin, requireAuthenticated } from "@/auth/admin-guard";
import { ME_QUERY_KEY, type Me } from "@/auth/useMe";

function clientWithMe(me: Me | null) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  qc.setQueryData(ME_QUERY_KEY, me);
  return qc;
}

const redirectMatcher = expect.objectContaining({ isRedirect: true });

vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<object>("@tanstack/react-router");
  return {
    ...actual,
    redirect: (opts: unknown) => {
      const err = new Error("redirect") as Error & { isRedirect: boolean; opts: unknown };
      err.isRedirect = true;
      err.opts = opts;
      return err;
    },
  };
});

describe("requireAdmin", () => {
  it("redirects unauthenticated to /login", async () => {
    const queryClient = clientWithMe(null);
    await expect(requireAdmin({ queryClient })).rejects.toMatchObject({
      isRedirect: true,
      opts: { to: "/login" },
    });
  });

  it("redirects non-admin to /", async () => {
    const queryClient = clientWithMe({
      kind: "user",
      username: "manager1",
      role: "manager",
    });
    await expect(requireAdmin({ queryClient })).rejects.toMatchObject({
      isRedirect: true,
      opts: { to: "/" },
    });
  });

  it("allows admin through", async () => {
    const queryClient = clientWithMe({
      kind: "user",
      username: "alice",
      role: "admin",
    });
    await expect(requireAdmin({ queryClient })).resolves.toBeUndefined();
  });

  it("allows root token (synthesised admin)", async () => {
    const queryClient = clientWithMe({
      kind: "root",
      username: null,
      role: "admin",
    });
    await expect(requireAdmin({ queryClient })).resolves.toBeUndefined();
  });
});

describe("requireAuthenticated", () => {
  it("redirects unauthenticated to /login", async () => {
    const queryClient = clientWithMe(null);
    await expect(requireAuthenticated({ queryClient })).rejects.toMatchObject(redirectMatcher);
  });

  it("allows any role through", async () => {
    const queryClient = clientWithMe({
      kind: "user",
      username: "kid1",
      role: "member",
    });
    await expect(requireAuthenticated({ queryClient })).resolves.toBeUndefined();
  });
});
