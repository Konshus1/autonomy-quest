import { defineConfig, devices } from "@playwright/test";

// Target the running management API (React shell + FastAPI). Point at a live container:
//   AQ_MGMT_URL=http://localhost:18195 npm test
// Default matches the OOTB container's mapped mgmt port used in the #4407 checkpoints.
const baseURL = process.env.AQ_MGMT_URL || "http://localhost:18195";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  reporter: [["list"], ["json", { outputFile: "results/results.json" }]],
  use: {
    baseURL,
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
