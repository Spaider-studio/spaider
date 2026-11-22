import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getAgents: vi.fn(),
  getGraph: vi.fn(),
  getGraphClusters: vi.fn(),
  getGraphStats: vi.fn(),
  getMultiverseGraph: vi.fn(),
}));

import { getAgents, getGraph, getGraphClusters, getGraphStats, getMultiverseGraph } from "@/lib/api";
import { useGraph } from "./useGraph";

const agents = [
  { id: "kb-1", name: "Alpha" },
  { id: "kb-2", name: "Beta" },
] as never;

beforeEach(() => {
  vi.mocked(getAgents).mockReset();
  vi.mocked(getGraph).mockReset();
  vi.mocked(getGraphClusters).mockReset();
  vi.mocked(getGraphStats).mockReset();
  vi.mocked(getMultiverseGraph).mockReset();
  useGraph.setState({ agentId: null, agents: [], agentsError: null, nodes: [], stats: null });
});

describe("choosing a knowledge base", () => {
  it("only records the choice, so pages without a graph do not load one", () => {
    // Every pick in the header used to fetch the graph, the clusters and the
    // statistics, on every page, whether or not the page shows a graph.
    useGraph.getState().setAgentId("kb-2");

    expect(useGraph.getState().agentId).toBe("kb-2");
    expect(getGraph).not.toHaveBeenCalled();
    expect(getMultiverseGraph).not.toHaveBeenCalled();
    expect(getGraphStats).not.toHaveBeenCalled();
    expect(getGraphClusters).not.toHaveBeenCalled();
  });

  it("drops the previous knowledge base's graph so it is never shown for the new one", () => {
    useGraph.setState({ agentId: "kb-1", nodes: [{ id: "n1" } as never], stats: {} as never });

    useGraph.getState().setAgentId("kb-2");

    expect(useGraph.getState().nodes).toEqual([]);
    expect(useGraph.getState().stats).toBeNull();
  });
});

describe("the list of knowledge bases", () => {
  it("is fetched once however many components ask for it", async () => {
    vi.mocked(getAgents).mockResolvedValue(agents);

    await Promise.all([useGraph.getState().loadAgents(), useGraph.getState().loadAgents(), useGraph.getState().loadAgents()]);

    expect(getAgents).toHaveBeenCalledTimes(1);
    expect(useGraph.getState().agents).toEqual(agents);
  });

  it("is not fetched again once it is there", async () => {
    vi.mocked(getAgents).mockResolvedValue(agents);
    await useGraph.getState().loadAgents();

    await useGraph.getState().loadAgents();

    expect(getAgents).toHaveBeenCalledTimes(1);
  });

  it("keeps the error, and can be tried again", async () => {
    vi.mocked(getAgents).mockRejectedValueOnce(new Error("API rate limit exceeded")).mockResolvedValueOnce(agents);

    await useGraph.getState().loadAgents();
    expect(useGraph.getState().agentsError).toBe("API rate limit exceeded");
    expect(useGraph.getState().agents).toEqual([]);

    await useGraph.getState().loadAgents(true);

    expect(useGraph.getState().agentsError).toBeNull();
    expect(useGraph.getState().agents).toEqual(agents);
  });

  it("forgets a choice that no longer exists", async () => {
    useGraph.setState({ agentId: "deleted-kb" });
    vi.mocked(getAgents).mockResolvedValue(agents);

    await useGraph.getState().loadAgents();

    expect(useGraph.getState().agentId).toBeNull();
  });
});
