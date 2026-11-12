import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev: the API is proxied under /api, exactly like the nginx gateway does in docker compose.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    fs: { allow: [".."] },
    proxy: { "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/api/, ""), changeOrigin: false } },
  },
  test: { environment: "node", include: ["test/**/*.test.ts"] },
});
