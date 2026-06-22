import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";

const host = process.env.PLAYWRIGHT_HOST || "127.0.0.1";
const port = process.env.PLAYWRIGHT_PORT || "4173";
const baseURL = `http://${host}:${port}`;
const outputDir =
  process.env.PLAYWRIGHT_OUTPUT_DIR ||
  resolve("test-results", `playwright-${Date.now()}-${process.pid}`);

mkdirSync(outputDir, { recursive: true });

const server = spawn(
  process.execPath,
  [join("e2e", "static-server.mjs"), "--host", host, "--port", port, "--root", "dist"],
  { cwd: process.cwd(), stdio: "inherit" }
);

let stopping = false;

try {
  await waitForServer(baseURL);
  const exitCode = await run(
    process.execPath,
    [join("node_modules", "playwright", "cli.js"), "test"],
    {
      ...process.env,
      PLAYWRIGHT_SKIP_WEB_SERVER: "1",
      PLAYWRIGHT_OUTPUT_DIR: outputDir
    }
  );
  process.exitCode = exitCode;
} finally {
  stopping = true;
  server.kill();
}

server.on("exit", (code) => {
  if (!stopping && code !== 0) {
    process.exitCode = code ?? 1;
  }
});

function run(command, args, env) {
  return new Promise((resolveRun) => {
    const child = spawn(command, args, { cwd: process.cwd(), env, stdio: "inherit" });
    child.on("exit", (code) => resolveRun(code ?? 1));
  });
}

async function waitForServer(url) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) {
        return;
      }
    } catch {
      // Server is still starting.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
  }
  throw new Error(`Timed out waiting for ${url}`);
}
