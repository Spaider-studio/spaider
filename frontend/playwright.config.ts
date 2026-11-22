import { defineConfig } from "@playwright/test";

// These tests drive the Studio of the test rig (backend/tests/rig/rig.py), never
// the real stack. e2e/rig.ts refuses any other address.
export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  // The rig keeps the shipped limit of 60 requests a minute per key, so tests
  // must not run in parallel on the same key.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:13000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
});
