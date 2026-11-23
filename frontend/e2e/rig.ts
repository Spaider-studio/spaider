import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, type Page } from "@playwright/test";

const RIG_STUDIO = "http://localhost:13000";
const KEYS_FILE = path.resolve(__dirname, "../../backend/tests/rig/.rig-keys.json");

export type Rig = {
  keys: Record<string, string>;
  kbs: Record<string, string>;
};

/** Keys and KB ids written by backend/tests/rig/seed.py. Never printed. */
export function rig(): Rig {
  return JSON.parse(readFileSync(KEYS_FILE, "utf-8")) as Rig;
}

/** What a test run saw that it should not have. */
export type Watch = {
  consoleErrors: string[];
  failedRequests: string[];
  apiRequests: string[];
};

/** Records console errors, 4xx/5xx API answers and every API request of a page. */
export function watch(page: Page): Watch {
  const seen: Watch = { consoleErrors: [], failedRequests: [], apiRequests: [] };
  page.on("console", (message) => {
    if (message.type() === "error") seen.consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => seen.consoleErrors.push(`pageerror: ${error.message}`));
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (!url.pathname.startsWith("/api/") && !url.port.startsWith("18")) return;
    seen.apiRequests.push(`${response.request().method()} ${url.pathname}`);
    if (response.status() >= 400) {
      seen.failedRequests.push(`${response.status()} ${response.request().method()} ${url.pathname}`);
    }
  });
  return seen;
}

/** Signs in through the real login form with one of the seeded keys. */
export async function login(page: Page, who: string): Promise<void> {
  const base = process.env.RIG_STUDIO ?? RIG_STUDIO;
  if (base !== RIG_STUDIO) throw new Error(`refusing to drive ${base}: it is not the test rig`);
  await page.goto("/login");
  await page.getByLabel("API key").fill(rig().keys[who]);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/studio/);
}

/** Runs docker inside the rig container, to stop and start its services. It can only reach the rig. */
export function rigDocker(...args: string[]): string {
  return execFileSync("docker", ["exec", "spaider-rig", "docker", ...args], { encoding: "utf-8" });
}
