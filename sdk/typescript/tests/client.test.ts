import { describe, expect, it, vi } from "vitest";
import { SpaiderClient, SpaiderError } from "../src/index";

function setup(status = 200, body: unknown = { ok: true }) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  const client = new SpaiderClient({
    baseUrl: "http://h/",
    apiKey: "ss-k",
    agentId: "kb1",
    fetch: fetch as unknown as typeof globalThis.fetch,
  });
  const last = () => {
    const [url, init] = fetch.mock.calls.at(-1) as unknown as [string, RequestInit];
    return { url, method: init.method, headers: init.headers as Record<string, string>, body: init.body ? JSON.parse(init.body as string) : undefined };
  };
  return { client, last };
}

describe("SpaiderClient", () => {
  it("needs a key", () => {
    expect(() => new SpaiderClient({ baseUrl: "http://h", apiKey: "" })).toThrow();
  });

  it("sends the key and strips trailing slashes", async () => {
    const { client, last } = setup();
    await client.recall("door");
    expect(last().url).toBe("http://h/api/v1/recall");
    expect(last().headers["X-Api-Key"]).toBe("ss-k");
  });

  it("writes episodes", async () => {
    const { client, last } = setup();
    await client.remember("saw a door", { kind: "vision" });
    expect(last().body).toEqual({ agent_id: "kb1", episodes: [{ content: "saw a door", kind: "vision" }] });
  });

  it("passes recall filters", async () => {
    const { client, last } = setup();
    await client.recall("x", { limit: 3, federated: true });
    expect(last().body).toEqual({ agent_id: "kb1", query: "x", limit: 3, federated: true });
  });

  it("sends feedback to the system route", async () => {
    const { client, last } = setup();
    await client.feedback("q1", false);
    expect(last().url).toBe("http://h/api/v1/system/feedback");
    expect(last().body.used_node_ids).toEqual([]);
  });

  it("targets its own KB when requesting a grant", async () => {
    const { client, last } = setup();
    await client.requestGrant("other", { max_clearance: 2 });
    expect(last().body).toEqual({ source_kb: "other", target_kb: "kb1", max_clearance: 2 });
  });

  it("escapes ids in paths", async () => {
    const { client, last } = setup();
    await client.approveGrant("a/b");
    expect(last().url).toBe("http://h/api/v1/federation/grants/a%2Fb/approve");
  });

  it("puts query parameters in the URL", async () => {
    const { client, last } = setup();
    await client.listKprs();
    expect(last().url).toBe("http://h/api/v1/federation/kpr?target_kb=kb1");
  });

  it("raises with the server's reason", async () => {
    const { client } = setup(403, { detail: "Not permitted" });
    await expect(client.revertKpr("k")).rejects.toMatchObject({ status: 403, detail: "Not permitted" });
    await expect(client.revertKpr("k")).rejects.toBeInstanceOf(SpaiderError);
  });

  it("needs a KB for KB calls", async () => {
    const client = new SpaiderClient({ baseUrl: "http://h", apiKey: "k", fetch: vi.fn() as never });
    expect(() => client.recall("x")).toThrow(/agentId/);
  });
});
