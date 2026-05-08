// Thin fetch wrapper around the curfew API.
//
// Same-origin: dev uses Vite's proxy (/v1 -> :8000); prod is FastAPI serving
// the SPA on the same origin. `credentials: "include"` is enough; no CORS
// dance, no manual cookie handling.
//
// Returns parsed JSON for 2xx, throws ApiError otherwise. Calling code reads
// the status off ApiError to render specific messages (e.g. 401 on login).
//
// We deliberately keep this thin — calling sites import schema types from
// ./schema and supply them at the call site. A heavier abstraction (orval,
// openapi-fetch) is justified later when manual typing gets repetitive.

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
  ) {
    super(`API ${status}: ${typeof body === "string" ? body : JSON.stringify(body)}`);
    this.name = "ApiError";
    // ES2015 + TS-down-compiled constructors break instanceof without this.
    Object.setPrototypeOf(this, ApiError.prototype);
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE" | "PUT";
  body?: unknown;
  signal?: AbortSignal;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal } = options;
  const init: RequestInit = {
    method,
    credentials: "include",
    signal,
  };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }

  const response = await fetch(path, init);
  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    throw new ApiError(response.status, payload);
  }
  return payload as T;
}
