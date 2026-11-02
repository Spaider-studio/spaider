/**
 * Dev launcher: waits for the Vite dev server, then starts Electron with
 * --app-root=dev-data and VITE_DEV_SERVER_URL. With `--mock` the attach-mode
 * variables point at the mock core started by `npm run dev:mock`.
 */
import { spawn } from "node:child_process";
import path from "node:path";
import electronPath from "electron";

const desktopDir = path.resolve(import.meta.dirname, "..");
const devServerUrl = process.env.VITE_DEV_SERVER_URL ?? "http://localhost:5173";
const useMock = process.argv.includes("--mock");

async function waitForVite(url, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(1500) });
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`Vite dev server not reachable at ${url}`);
}

await waitForVite(devServerUrl);

const env = { ...process.env, VITE_DEV_SERVER_URL: devServerUrl };
// Some terminals (VS Code, agent harnesses) export ELECTRON_RUN_AS_NODE=1,
// which would turn Electron into a plain Node binary.
delete env.ELECTRON_RUN_AS_NODE;
if (useMock) {
  env.SPAIDER_FOLDABLE_CORE_URL ??= "http://127.0.0.1:8765";
  env.SPAIDER_FOLDABLE_CORE_TOKEN ??= "mock-token";
}

const child = spawn(electronPath, [".", "--app-root=dev-data"], {
  cwd: desktopDir,
  env,
  stdio: "inherit",
  windowsHide: false,
});
child.on("exit", (code) => process.exit(code ?? 0));
