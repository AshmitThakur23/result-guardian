import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against the **real NODE A stack**: Caddy on :80 serving `web/dist`
 * and proxying `/api` to the FastAPI container, backed by the real Postgres.
 *
 * No `webServer` block, because the stack is long-running (`docker compose up
 * -d`) rather than something a test run should start and stop. `npm run e2e`
 * rebuilds `dist` first -- Caddy mounts it from the host, so a build is all
 * that is needed to put new UI in front of the browser.
 */
export default defineConfig({
  testDir: "./e2e",
  // Clean the database on the way in and on the way out. Every row this suite
  // creates is tagged, and nothing else is touched.
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "list" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.RG_E2E_BASE_URL ?? "http://localhost",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // The gate displays IST. Pinning it keeps deadline assertions stable
    // wherever the suite runs.
    timezoneId: "Asia/Kolkata",
    locale: "en-IN",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
