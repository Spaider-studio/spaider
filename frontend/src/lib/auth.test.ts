import { beforeEach, describe, expect, it, vi } from "vitest";
import { authFetch, clearApiKey, connectionTicket, getApiKey, setApiKey } from "./auth";

beforeEach(() => {
  clearApiKey();
  vi.restoreAllMocks();
});

function stubFetch(status: number, body: unknown = {}) {
  const mock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("auth", () => {
  it("keeps the key for the session only", () => {
    setApiKey("sk-1");
    expect(window.sessionStorage.getItem("spaider.apiKey")).toBe("sk-1");
    expect(window.localStorage.getItem("spaider.apiKey")).toBeNull();
  });

  it("sends the key as a header", async () => {
    setApiKey("sk-1");
    const mock = stubFetch(200);
    await authFetch("/api/v1/x", { headers: { "Content-Type": "application/json" } });
    const init = (mock.mock.calls[0] as unknown as [string, RequestInit])[1];
    const headers = new Headers(init.headers);
    expect(headers.get("X-Api-Key")).toBe("sk-1");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("sends no header without a key", async () => {
    const mock = stubFetch(200);
    await authFetch("/api/v1/x");
    const init = (mock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(new Headers(init.headers).has("X-Api-Key")).toBe(false);
  });

  it("forgets a rejected key", async () => {
    setApiKey("sk-old");
    stubFetch(401);
    const assign = vi.fn();
    vi.stubGlobal("location", { pathname: "/studio", assign });
    await authFetch("/api/v1/x");
    expect(getApiKey()).toBeNull();
    expect(assign).toHaveBeenCalledWith("/login");
  });

  it("does not put the key in a ticket URL", async () => {
    setApiKey("sk-1");
    const mock = stubFetch(201, { ticket: "tk-abc" });
    await expect(connectionTicket()).resolves.toBe("tk-abc");
    expect((mock.mock.calls[0] as unknown as [string])[0]).toBe("/api/v1/auth/ticket");
  });
});
