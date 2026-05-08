import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import path from "node:path";

// Vite + Vitest config in one place. Two-config setups hit a "duplicate
// Vite" type clash because vitest/node_modules ships its own copy of vite —
// merging here avoids that entirely.
//
// Dev proxy: /v1/* requests forward to the FastAPI dev server on :8000 so
// the browser sees one origin and cookies + auth Just Work without CORS.
// In production the SPA is served by FastAPI itself (StaticFiles mount),
// so the same origin assumption holds — see ADR-017.
export default defineConfig({
  plugins: [TanStackRouterVite({ target: "react", autoCodeSplitting: true }), react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
  test: {
    globals: true,
    environment: "happy-dom",
    setupFiles: ["./src/setupTests.ts"],
    css: false,
  },
});
