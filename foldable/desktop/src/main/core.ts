/**
 * Core process management: spawns (or attaches to) the Python core, reads the
 * `{"event":"ready","port":N}` line from stdout, captures stderr into a
 * rotating log, publishes backend status events and performs the graceful
 * shutdown sequence (POST /api/system/shutdown -> wait -> taskkill).
 */
import { spawn, type ChildProcess } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import type { BackendInfo, BackendStatus } from "../shared-types/bridge";
import { RotatingFileLog, log } from "./log";
import type { AppPaths } from "./paths";

export type StatusListener = (s: BackendStatus) => void;

const DEV_VENV_PYTHON = "C:\\Users\\mucbay\\spaider-foldable\\.venv\\Scripts\\python.exe";
const STDERR_TAIL_LINES = 40;
const SHUTDOWN_REQUEST_TIMEOUT_MS = 5000;
const SHUTDOWN_EXIT_TIMEOUT_MS = 20000;
const TASKKILL_EXIT_TIMEOUT_MS = 5000;
const ATTACH_HEALTH_TIMEOUT_MS = 30000;

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function coreRequest(
  method: "GET" | "POST",
  url: string,
  token: string,
  timeoutMs: number,
): Promise<Response> {
  return fetch(url, {
    method,
    headers: { "X-SpAIder-Token": token, Accept: "application/json" },
    signal: AbortSignal.timeout(timeoutMs),
  });
}

export class CoreManager {
  private token: string;
  private status: BackendStatus = { state: "starting", message: "SpAIder startet …" };
  private backend: BackendInfo | null = null;
  private readonly listeners = new Set<StatusListener>();
  private readonly readyPromise: Promise<BackendInfo>;
  private resolveReady!: (b: BackendInfo) => void;
  private rejectReady!: (e: Error) => void;
  private readySettled = false;

  private child: ChildProcess | null = null;
  private attached = false;
  private exited = false;
  private stopping = false;
  private stopPromise: Promise<void> | null = null;
  private readonly stderrTail: string[] = [];
  private readonly stderrLog: RotatingFileLog;
  private readonly exitWaiters = new Set<() => void>();

  constructor(private readonly paths: AppPaths) {
    this.token = crypto.randomBytes(32).toString("hex");
    this.stderrLog = new RotatingFileLog(path.join(paths.logsDir, "core-stderr.log"));
    this.readyPromise = new Promise<BackendInfo>((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
    });
    // Avoid an unhandled rejection when nobody awaits getBackend() yet.
    this.readyPromise.catch(() => undefined);
  }

  getStatus(): BackendStatus {
    return this.status;
  }

  getBackend(): Promise<BackendInfo> {
    return this.readyPromise;
  }

  getBackendInfoSync(): BackendInfo | null {
    return this.backend;
  }

  onStatus(cb: StatusListener): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  /** Current backend status (used by the screenshot verification mode). */
  currentStatus(): BackendStatus {
    return this.status;
  }

  private setStatus(s: BackendStatus): void {
    this.status = s;
    log(`backend status: ${s.state}${s.message ? ` - ${s.message.split("\n")[0]}` : ""}`);
    for (const cb of this.listeners) {
      try {
        cb(s);
      } catch {
        /* ignore listener errors */
      }
    }
  }

  private markReady(baseUrl: string): void {
    this.backend = { baseUrl, token: this.token };
    this.setStatus({ state: "ready" });
    if (!this.readySettled) {
      this.readySettled = true;
      this.resolveReady(this.backend);
    }
  }

  private fail(message: string): void {
    if (this.stopping) return;
    this.setStatus({ state: "error", message });
    if (!this.readySettled) {
      this.readySettled = true;
      this.rejectReady(new Error(message));
    }
  }

  async start(): Promise<void> {
    // Attach mode is an explicit developer opt-in via both env variables; it is
    // honoured packaged too so the exe can be verified against the mock core.
    const attachUrl = process.env.SPAIDER_FOLDABLE_CORE_URL;
    const attachToken = process.env.SPAIDER_FOLDABLE_CORE_TOKEN;
    if (attachUrl && attachToken) {
      await this.attach(attachUrl.replace(/\/+$/, ""), attachToken);
      return;
    }
    this.spawnCore();
  }

  /** Dev only: use an already-running core instead of spawning one. */
  private async attach(baseUrl: string, token: string): Promise<void> {
    this.attached = true;
    this.token = token;
    this.setStatus({ state: "starting", message: `Verbinde mit laufendem Kern (${baseUrl}) …` });
    const deadline = Date.now() + ATTACH_HEALTH_TIMEOUT_MS;
    let lastError = "";
    while (Date.now() < deadline) {
      try {
        const res = await coreRequest("GET", `${baseUrl}/api/health`, token, 2000);
        if (res.ok) {
          log(`attached to running core at ${baseUrl}`);
          this.markReady(baseUrl);
          return;
        }
        lastError = `HTTP ${res.status}`;
        if (res.status === 401) break;
      } catch (e) {
        lastError = e instanceof Error ? e.message : String(e);
      }
      await sleep(500);
    }
    this.fail(`Kein laufender SpAIder-Kern unter ${baseUrl} erreichbar.\n${lastError}`);
  }

  private spawnCore(): void {
    let command: string;
    let args: string[];
    let cwd: string;
    let runtimeDir: string | undefined;

    if (this.paths.isPackaged) {
      const exe = path.join(process.resourcesPath, "core", "spaider-foldable-core.exe");
      if (!fs.existsSync(exe)) {
        this.fail(`Der SpAIder-Kern wurde nicht gefunden:\n${exe}`);
        return;
      }
      command = exe;
      args = ["--app-root", this.paths.appRoot];
      cwd = path.dirname(exe);
      runtimeDir = path.join(process.resourcesPath, "runtime", "llama");
    } else {
      command = fs.existsSync(DEV_VENV_PYTHON) ? DEV_VENV_PYTHON : "python";
      args = ["-m", "spaider_foldable", "--app-root", this.paths.appRoot];
      cwd = path.resolve(__dirname, "..", "..", "..", "core");
      runtimeDir = process.env.SPAIDER_FOLDABLE_RUNTIME_DIR;
    }

    const env: NodeJS.ProcessEnv = {
      ...process.env,
      SPAIDER_FOLDABLE_TOKEN: this.token,
      PYTHONUNBUFFERED: "1",
      PYTHONIOENCODING: "utf-8",
    };
    if (runtimeDir) env.SPAIDER_FOLDABLE_RUNTIME_DIR = runtimeDir;

    log(`spawning core: ${command} ${args.join(" ")} (cwd ${cwd})`);
    this.setStatus({ state: "starting", message: "SpAIder-Kern wird gestartet …" });

    let child: ChildProcess;
    try {
      child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    } catch (e) {
      this.fail(`Der SpAIder-Kern konnte nicht gestartet werden:\n${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    this.child = child;

    child.on("error", (err) => {
      log(`core spawn error: ${err.message}`);
      this.fail(`Der SpAIder-Kern konnte nicht gestartet werden:\n${err.message}`);
    });

    if (child.stdout) {
      const rl = readline.createInterface({ input: child.stdout });
      rl.on("line", (line) => this.handleStdoutLine(line));
    }
    if (child.stderr) {
      const rl = readline.createInterface({ input: child.stderr });
      rl.on("line", (line) => {
        this.stderrLog.write(`${line}\n`);
        this.stderrTail.push(line);
        if (this.stderrTail.length > STDERR_TAIL_LINES) this.stderrTail.shift();
      });
    }

    child.on("exit", (code, signal) => {
      this.exited = true;
      this.child = null;
      log(`core exited (code ${code}, signal ${signal})`);
      for (const w of this.exitWaiters) w();
      this.exitWaiters.clear();
      if (!this.stopping) {
        const tail = this.stderrTail.slice(-12).join("\n");
        this.fail(
          `Der SpAIder-Kern wurde unerwartet beendet (Code ${code ?? signal ?? "?"}).` +
            (tail ? `\n\nLetzte Meldungen:\n${tail}` : ""),
        );
      }
    });
  }

  private handleStdoutLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) return;
    if (trimmed.startsWith("{")) {
      try {
        const msg = JSON.parse(trimmed) as { event?: string; port?: number; message?: string };
        if (msg.event === "ready" && typeof msg.port === "number") {
          this.markReady(`http://127.0.0.1:${msg.port}`);
          return;
        }
        if (msg.event === "status" && typeof msg.message === "string" && !this.backend) {
          this.setStatus({ state: "starting", message: msg.message });
          return;
        }
      } catch {
        /* not JSON, fall through */
      }
    }
    this.stderrLog.write(`[stdout] ${line}\n`);
  }

  private waitForExit(timeoutMs: number): Promise<boolean> {
    if (this.exited || !this.child) return Promise.resolve(true);
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.exitWaiters.delete(onExit);
        resolve(false);
      }, timeoutMs);
      const onExit = () => {
        clearTimeout(timer);
        resolve(true);
      };
      this.exitWaiters.add(onExit);
    });
  }

  /** Graceful shutdown; never throws and never hangs beyond ~30 s. */
  shutdown(): Promise<void> {
    if (this.stopPromise) return this.stopPromise;
    this.stopping = true;
    this.stopPromise = this.doShutdown().catch((e) => {
      log(`shutdown error: ${e instanceof Error ? e.message : String(e)}`);
    });
    return this.stopPromise;
  }

  private async doShutdown(): Promise<void> {
    this.setStatus({ state: "stopping", message: "SpAIder wird beendet …" });
    const base = this.backend?.baseUrl;

    if (this.attached) {
      if (base) {
        try {
          await coreRequest("POST", `${base}/api/system/shutdown`, this.token, 3000);
          log("attach mode: shutdown requested from the external core");
        } catch (e) {
          log(`attach mode: shutdown request failed (${e instanceof Error ? e.message : String(e)})`);
        }
      }
      return;
    }

    if (!this.child || this.exited) {
      log("shutdown: core already exited, nothing to do");
      return;
    }
    const pid = this.child.pid;

    let requested = false;
    if (base) {
      try {
        const res = await coreRequest("POST", `${base}/api/system/shutdown`, this.token, SHUTDOWN_REQUEST_TIMEOUT_MS);
        requested = res.ok;
        log(`shutdown request -> HTTP ${res.status}`);
      } catch (e) {
        log(`shutdown request failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    }

    if (requested && (await this.waitForExit(SHUTDOWN_EXIT_TIMEOUT_MS))) {
      log("core stopped via graceful shutdown");
      return;
    }
    if (!requested && (await this.waitForExit(2000))) {
      log("core stopped on its own");
      return;
    }

    if (pid) {
      log(`core did not exit in time, running taskkill /PID ${pid} /T /F`);
      await new Promise<void>((resolve) => {
        const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
        killer.on("exit", () => resolve());
        killer.on("error", () => {
          try {
            this.child?.kill("SIGKILL");
          } catch {
            /* ignore */
          }
          resolve();
        });
      });
      const dead = await this.waitForExit(TASKKILL_EXIT_TIMEOUT_MS);
      log(dead ? "core stopped via taskkill" : "core still running after taskkill (giving up)");
    }
  }
}
