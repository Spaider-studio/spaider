import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/audit" }));
vi.mock("./CommandPalette", () => ({ default: () => null }));
vi.mock("@/lib/api", () => ({
  getAgents: vi.fn(),
  getGraph: vi.fn(),
  getGraphClusters: vi.fn(),
  getGraphStats: vi.fn(),
  getMultiverseGraph: vi.fn(),
}));

import { getAgents, getGraph, getGraphStats, getMultiverseGraph } from "@/lib/api";
import { useGraph } from "@/hooks/useGraph";
import Header from "./Header";

const two = [
  { id: "kb-1", name: "Alpha" },
  { id: "kb-2", name: "Beta" },
] as never;

beforeEach(() => {
  vi.mocked(getAgents).mockReset();
  vi.mocked(getGraph).mockReset();
  vi.mocked(getGraphStats).mockReset();
  vi.mocked(getMultiverseGraph).mockReset();
  useGraph.setState({ agentId: null, agents: [], agentsError: null, nodes: [], edges: [] });
});

describe("Header knowledge base picker", () => {
  it("shows 'all knowledge bases' when none is chosen, not the first one", async () => {
    // It used to list the knowledge bases without that option, so the field showed
    // the first one while nothing was selected and the Studio showed them all.
    vi.mocked(getAgents).mockResolvedValue(two);

    render(<Header />);
    const select = (await screen.findByLabelText("Knowledge base")) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));

    expect(select.value).toBe("");
    expect(select.options[0].textContent).toBe("All knowledge bases");
  });

  it("records the choice for the whole app without loading a graph", async () => {
    vi.mocked(getAgents).mockResolvedValue(two);
    render(<Header />);
    const select = (await screen.findByLabelText("Knowledge base")) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));

    fireEvent.change(select, { target: { value: "kb-2" } });

    expect(useGraph.getState().agentId).toBe("kb-2");
    expect(getGraph).not.toHaveBeenCalled();
    expect(getMultiverseGraph).not.toHaveBeenCalled();
    expect(getGraphStats).not.toHaveBeenCalled();
  });

  it("can go back to all knowledge bases", async () => {
    vi.mocked(getAgents).mockResolvedValue(two);
    useGraph.setState({ agentId: "kb-1" });
    render(<Header />);
    const select = (await screen.findByLabelText("Knowledge base")) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));

    fireEvent.change(select, { target: { value: "" } });

    expect(useGraph.getState().agentId).toBeNull();
  });

  it("names the knowledge base when there is only one, instead of printing its id", async () => {
    vi.mocked(getAgents).mockResolvedValue([{ id: "d5f139aa-a873-4b29", name: "Alpha" }] as never);

    render(<Header />);

    expect(await screen.findByText("Alpha")).toBeTruthy();
    expect(screen.queryByText(/d5f139aa/)).toBeNull();
  });

  it("does not show node and edge counts of a graph the page does not display", async () => {
    vi.mocked(getAgents).mockResolvedValue(two);
    useGraph.setState({ nodes: [{ id: "a" }, { id: "b" }] as never, edges: [] });

    render(<Header />);
    await screen.findByLabelText("Knowledge base");

    expect(screen.queryByText("N")).toBeNull();
    expect(screen.queryByText("E")).toBeNull();
  });
});
