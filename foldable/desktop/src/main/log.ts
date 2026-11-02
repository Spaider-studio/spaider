/**
 * Tiny append-only logger with size-based rotation (no dependencies).
 * Used for the launcher log and for capturing the stderr of the core.
 */
import fs from "node:fs";
import path from "node:path";

const ROTATE_BYTES = 5 * 1024 * 1024;

export class RotatingFileLog {
  private readonly file: string;
  private size = 0;

  constructor(file: string) {
    this.file = file;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    try {
      this.size = fs.statSync(file).size;
    } catch {
      this.size = 0;
    }
  }

  /** Appends raw text (caller adds newlines). */
  write(text: string): void {
    try {
      const bytes = Buffer.byteLength(text);
      if (this.size + bytes > ROTATE_BYTES) this.rotate();
      fs.appendFileSync(this.file, text);
      this.size += bytes;
    } catch {
      /* logging must never throw */
    }
  }

  line(text: string): void {
    this.write(`${new Date().toISOString()} ${text}\n`);
  }

  private rotate(): void {
    const old = `${this.file}.1`;
    try {
      fs.rmSync(old, { force: true });
      fs.renameSync(this.file, old);
    } catch {
      /* ignore */
    }
    this.size = 0;
  }
}

let launcherLog: RotatingFileLog | null = null;

export function initLauncherLog(logsDir: string): void {
  launcherLog = new RotatingFileLog(path.join(logsDir, "launcher.log"));
}

export function log(text: string): void {
  console.log(`[launcher] ${text}`);
  launcherLog?.line(text);
}
