import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./test-utils/server";

// Node 25 ships an experimental `localStorage` global that's a plain Object
// (no removeItem/clear methods). It shadows happy-dom's real Storage in test
// runs. Install a tiny in-memory Storage that satisfies the API contract
// for both `window.localStorage` and the bare `localStorage` global.
function installFakeStorage(): void {
  let store: Record<string, string> = {};
  const fake: Storage = {
    get length() {
      return Object.keys(store).length;
    },
    clear() {
      store = {};
    },
    getItem(key) {
      return key in store ? store[key]! : null;
    },
    key(i) {
      return Object.keys(store)[i] ?? null;
    },
    removeItem(key) {
      delete store[key];
    },
    setItem(key, value) {
      store[key] = String(value);
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: fake,
  });
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: fake,
  });
}
installFakeStorage();

// Start a Mock Service Worker for the test session. Each test resets handlers
// to keep tests isolated; tests opt in to specific mocks via server.use(...).
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
