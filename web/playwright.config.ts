import { defineConfig } from "@playwright/test";

// The browser talks to the stack THROUGH the chaos proxy (:8081 -> web -> api).
export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: process.env.E2E_BASE ?? "http://localhost:8081", headless: true },
});
