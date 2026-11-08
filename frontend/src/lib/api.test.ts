import { beforeEach, describe, expect, it, vi } from "vitest";
import { approveGrant, decideKpr, deleteSwarmLink, listSwarmLinks } from "./api";
import { clearApiKey, setApiKey } from "./auth";

function stubFetch(body: unknown) {
  const mock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
  vi.stubGlobal("fetch", mock);
  return mock;
}

const lastCall = (mock: ReturnType<typeof stubFetch>) =>
  mock.mock.calls.at(-1) as unknown as [string, RequestInit];

beforeEach(() => {
  clearApiKey();
  setApiKey("sk-1");
});

describe("federation calls", () => {
  it("shows only approved grants as links", async () => {
    stubFetch([
      { id: "a", source_kb: "s", target_kb: "t", status: "approved" },
      { id: "b", source_kb: "s", target_kb: "u", status: "requested" },
    ]);
    const links = await listSwarmLinks();
    expect(links.map((link) => link.grant_id)).toEqual(["a"]);
    expect(links[0]).toMatchObject({ source_id: "t", target_id: "s" });
  });

  it("removing a link revokes its grant", async () => {
    const mock = stubFetch({});
    await deleteSwarmLink({ source_id: "t", source_name: "t", target_id: "s", target_name: "s", grant_id: "g1" });
    expect(lastCall(mock)[0]).toBe("/api/v1/federation/grants/g1");
    expect(lastCall(mock)[1].method).toBe("DELETE");
  });

  it("approves through the federation route", async () => {
    const mock = stubFetch({});
    await approveGrant("g1");
    expect(lastCall(mock)[0]).toBe("/api/v1/federation/grants/g1/approve");
  });

  it.each(["approve", "reject", "revert"] as const)("sends KPR decision %s", async (decision) => {
    const mock = stubFetch({});
    await decideKpr("k1", decision);
    expect(lastCall(mock)[0]).toBe(`/api/v1/federation/kpr/k1/${decision}`);
  });
});
