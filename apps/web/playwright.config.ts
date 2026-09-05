import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end against a running stack: Postgres, Redis, the API, and the web
 * app. The web server is started here; the API is expected to be up already
 * (make up && make migrate && make seed && make dev-api).
 */
const webPort = Number(process.env.E2E_WEB_PORT ?? 3000);
const baseURL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.E2E_NO_WEBSERVER
    ? undefined
    : {
        command: "pnpm dev",
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
});
