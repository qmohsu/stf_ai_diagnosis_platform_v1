import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  // The mock V3 has one active diagnosis per vehicle, matching production.
  // Keep viewport projects sequential so they do not start the same car at once.
  workers: 1,
  timeout: 45_000,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3001",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "mobile-touch-360", use: { ...devices["Pixel 7"], viewport: { width: 360, height: 800 }, extraHTTPHeaders: { "x-real-ip": "198.51.100.10" } } },
    { name: "mobile-touch-390", use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 }, extraHTTPHeaders: { "x-real-ip": "198.51.100.20" } } },
    { name: "desktop-1280", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 }, extraHTTPHeaders: { "x-real-ip": "198.51.100.30" } } },
  ],
  webServer: [
    {
      command: "node tests/fixtures/mock-v3.mjs",
      url: "http://127.0.0.1:4010/health",
      reuseExistingServer: process.env.PW_REUSE_SERVER === "1",
      timeout: 30_000,
    },
    {
      command: "npm run start",
      url: "http://127.0.0.1:3001/api/health",
      reuseExistingServer: process.env.PW_REUSE_SERVER === "1",
      timeout: 120_000,
      env: {
        PORT: "3001",
        STF_V3_API_BASE_URL: "http://127.0.0.1:4010",
        PORTAL_PUBLIC_ORIGIN: "http://127.0.0.1:3001",
      },
    },
  ],
});
