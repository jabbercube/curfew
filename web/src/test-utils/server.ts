import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";

// Default handlers — tests override per-spec via `server.use(...)`.
export const server = setupServer(
  http.get("/v1/auth/me", () => HttpResponse.json(null, { status: 401 })),
);

export { http, HttpResponse };
