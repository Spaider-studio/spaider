/**
 * Every call the Studio makes must exist in the API.
 *
 * The OpenAPI file in sdk/python/contract is generated from the backend and checked
 * in CI, so it is the API as it really is. A client function that points at a route
 * that does not exist (traverseGraph did, for as long as nobody called it) fails here
 * instead of in a customer's browser.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = path.resolve(__dirname, "../..");
const SPEC = path.resolve(ROOT, "../sdk/python/contract/openapi.json");

type Call = { method: string; path: string; file: string };

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [full] : [];
  });
}

/** `/agents/${id}/members` becomes `/agents/{}/members`; a query string or a trailing `${...}` is dropped. */
function normalise(raw: string): string {
  let text = raw.split("?")[0];
  text = text.replace(/\$\{[^}]*\}/g, (match, offset: number) =>
    offset > 0 && text[offset - 1] !== "/" ? "\u0000" : "{}"
  );
  // A `${` left over is a template with another template inside it, which is a query string.
  return text.split("\u0000")[0].split("${")[0];
}

function clientCalls(): Call[] {
  const calls: Call[] = [];
  for (const file of sourceFiles(path.join(ROOT, "src"))) {
    const text = readFileSync(file, "utf-8");
    // The rest of the statement only, so the next function's method is not read as this one's.
    const pattern = /\b(?:request|authFetch)\s*(?:<[^>]*>)?\(\s*[`"'](\/[^`"']*)[`"']([\s\S]{0,300}?)(?:\n\}|;\n)/g;
    for (const match of text.matchAll(pattern)) {
      // The Studio's own routes (health proxy), not the backend's.
      if (match[1].startsWith("/api/")) continue;
      const method = /method:\s*["'](\w+)["']/.exec(match[2])?.[1]?.toUpperCase() ?? "GET";
      calls.push({ method, path: normalise(match[1]), file: path.relative(ROOT, file) });
    }
  }
  return calls;
}

function specOperations(): { method: string; segments: string[] }[] {
  const spec = JSON.parse(readFileSync(SPEC, "utf-8")) as { paths: Record<string, Record<string, unknown>> };
  return Object.entries(spec.paths).flatMap(([route, operations]) =>
    Object.keys(operations)
      .filter((method) => ["get", "post", "put", "patch", "delete"].includes(method))
      .map((method) => ({
        method: method.toUpperCase(),
        segments: route.replace(/^\/api\/v1/, "").split("/").map((s) => (s.startsWith("{") ? "{}" : s)),
      }))
  );
}

function exists(call: Call, operations: ReturnType<typeof specOperations>): boolean {
  const wanted = call.path.split("/");
  return operations.some(
    (operation) =>
      operation.method === call.method &&
      operation.segments.length === wanted.length &&
      operation.segments.every((segment, index) => segment === wanted[index] || segment === "{}" || wanted[index] === "{}")
  );
}

describe("the Studio's API calls", () => {
  const calls = clientCalls();
  const operations = specOperations();

  it("finds the calls it is meant to check", () => {
    // A scan that finds nothing would pass for the wrong reason.
    expect(calls.length).toBeGreaterThan(40);
    expect(operations.length).toBeGreaterThan(80);
  });

  it("only calls routes that exist, with the method they accept", () => {
    const missing = calls.filter((call) => !exists(call, operations)).map((c) => `${c.method} ${c.path} (${c.file})`);

    expect(missing).toEqual([]);
  });
});
