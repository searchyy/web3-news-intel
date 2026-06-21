import { defineConfig, devices } from "@playwright/test";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const configDir = dirname(fileURLToPath(import.meta.url));
const host = process.env.PLAYWRIGHT_HOST || "127.0.0.1";
const port = Number(process.env.PLAYWRIGHT_PORT || "4173");
const baseURL = `http://${host}:${port}`;
const webServer = process.env.PLAYWRIGHT_SKIP_WEB_SERVER
  ? undefined
  : {
      command: `node ./e2e/static-server.mjs --host ${host} --port ${port} --root dist`,
      cwd: configDir,
      url: baseURL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000
    };

export default defineConfig({
  testDir: "./e2e",
  outputDir: process.env.PLAYWRIGHT_OUTPUT_DIR || "../artifacts/playwright-results",
  timeout: 30_000,
  expect: {
    timeout: 5_000
  },
  use: {
    baseURL,
    trace: "on-first-retry"
  },
  webServer,
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ]
});
