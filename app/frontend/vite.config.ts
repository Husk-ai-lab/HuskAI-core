import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// Shared message catalogues with the backend (app/i18n/locales).
const locales = fileURLToPath(new URL("../i18n/locales", import.meta.url));

// In development the UI runs on :5173 and proxies the API to FastAPI (HUSK_PORT, default 8765).
const backend = `http://127.0.0.1:${process.env.HUSK_PORT ?? "8765"}`;

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@i18n": locales } },
  server: {
    port: 5173,
    fs: { allow: [".", locales] },
    proxy: {
      "/api": { target: backend, changeOrigin: false },
      "/auth": { target: backend, changeOrigin: false },
      "/health": { target: backend, changeOrigin: false },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"] },
});
