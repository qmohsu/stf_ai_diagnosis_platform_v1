import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  timeout: 45_000,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3001",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "mobile-360", use: { ...devices["Desktop Chrome"], channel: "chrome", extraHTTPHeaders: { "x-forwarded-for": "198.51.100.10" }, viewport: { width: 360, height: 800 } } },
    { name: "mobile-390", use: { ...devices["Desktop Chrome"], channel: "chrome", extraHTTPHeaders: { "x-forwarded-for": "198.51.100.20" }, viewport: { width: 390, height: 844 } } },
    { name: "tablet-768", use: { ...devices["Desktop Chrome"], channel: "chrome", extraHTTPHeaders: { "x-forwarded-for": "198.51.100.30" }, viewport: { width: 768, height: 1024 } } },
    { name: "desktop-1280", use: { ...devices["Desktop Chrome"], channel: "chrome", extraHTTPHeaders: { "x-forwarded-for": "198.51.100.40" }, viewport: { width: 1280, height: 800 } } },
    { name: "desktop-1672", use: { ...devices["Desktop Chrome"], channel: "chrome", extraHTTPHeaders: { "x-forwarded-for": "198.51.100.50" }, viewport: { width: 1672, height: 941 } } },
  ],
  webServer: [
    {
      command: "node tests/fixtures/mock-twin.mjs",
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
        VEHICLE_TWIN_API_BASE_URL: "http://127.0.0.1:4010",
        STF_V3_API_BASE_URL: "http://127.0.0.1:4010",
        PORTAL_PUBLIC_ORIGIN: "http://127.0.0.1:3001",
        PORTAL_VEHICLE_SESSION_SECRET: "playwright-vehicle-session-secret-32-characters",
      },
    },
  ],
});
