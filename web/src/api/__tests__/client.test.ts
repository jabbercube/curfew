import { describe, expect, it } from "vitest";
import { ApiError, apiFetch } from "@/api/client";
import { http, HttpResponse, server } from "@/test-utils/server";

describe("apiFetch", () => {
  it("returns parsed JSON on 2xx", async () => {
    server.use(http.get("/v1/ping", () => HttpResponse.json({ ok: true })));
    await expect(apiFetch<{ ok: boolean }>("/v1/ping")).resolves.toEqual({ ok: true });
  });

  it("returns undefined on 204", async () => {
    server.use(http.post("/v1/ping", () => new HttpResponse(null, { status: 204 })));
    await expect(apiFetch<void>("/v1/ping", { method: "POST" })).resolves.toBeUndefined();
  });

  it("throws ApiError with status on non-2xx", async () => {
    server.use(http.get("/v1/ping", () => HttpResponse.json({ detail: "nope" }, { status: 401 })));
    await expect(apiFetch("/v1/ping")).rejects.toBeInstanceOf(ApiError);
  });

  it("attaches credentials by default", async () => {
    let saw: RequestCredentials | undefined;
    server.use(
      http.get("/v1/ping", ({ request }) => {
        saw = request.credentials;
        return HttpResponse.json({});
      }),
    );
    await apiFetch("/v1/ping");
    expect(saw).toBe("include");
  });
});
