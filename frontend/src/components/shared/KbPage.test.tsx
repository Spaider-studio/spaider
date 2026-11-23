import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getAgents: vi.fn(),
  getGraph: vi.fn(),
  getGraphClusters: vi.fn(),
  getGraphStats: vi.fn(),
  getMultiverseGraph: vi.fn(),
}));
vi.mock("@/components/layout/DashboardShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import { useGraph } from "@/hooks/useGraph";
import { getAgents } from "@/lib/api";
import KbPage from "./KbPage";

const agents = [
  { id: "kb-1", name: "Alpha" },
  { id: "kb-2", name: "Beta" },
] as never;

beforeEach(() => {
  // Braces matter: a function returned from beforeEach is called as cleanup.
  vi.mocked(getAgents).mockReset();
  useGraph.setState({ agentId: null, agents: [], agentsError: null });
});

const page = () => <KbPage title="Members">{(id) => <p>page for {id}</p>}</KbPage>;

describe("KbPage", () => {
  it("has no picker of its own: the knowledge base is chosen in one place", async () => {
    vi.mocked(getAgents).mockResolvedValue(agents);

    const { container } = render(page());
    await screen.findByText("page for kb-1");

    expect(container.querySelector("select")).toBeNull();
  });

  it("takes the first knowledge base when none is chosen, and records it so the header agrees", async () => {
    vi.mocked(getAgents).mockResolvedValue(agents);

    render(page());

    expect(await screen.findByText("page for kb-1")).toBeTruthy();
    expect(useGraph.getState().agentId).toBe("kb-1");
  });

  it("follows the choice made in the header", async () => {
    vi.mocked(getAgents).mockResolvedValue(agents);
    useGraph.setState({ agentId: "kb-2" });

    render(page());

    expect(await screen.findByText("page for kb-2")).toBeTruthy();
  });

  it("changes when the choice changes", async () => {
    vi.mocked(getAgents).mockResolvedValue(agents);
    render(page());
    await screen.findByText("page for kb-1");

    act(() => useGraph.getState().setAgentId("kb-2"));

    expect(await screen.findByText("page for kb-2")).toBeTruthy();
  });

  it("says which knowledge base it is showing, by name", async () => {
    vi.mocked(getAgents).mockResolvedValue(agents);
    useGraph.setState({ agentId: "kb-2" });

    render(page());

    expect(await screen.findByText("Beta")).toBeTruthy();
  });

  it("says so when there really are no knowledge bases", async () => {
    vi.mocked(getAgents).mockResolvedValue([]);

    render(page());

    expect(await screen.findByText("No knowledge bases yet.")).toBeTruthy();
  });

  it("says the list could not be loaded, instead of claiming there are none", async () => {
    // A rate limit or a server error used to look exactly like an empty install.
    vi.mocked(getAgents).mockRejectedValue(new Error("API rate limit exceeded"));

    render(page());

    expect(await screen.findByText(/Could not load your knowledge bases/)).toBeTruthy();
    expect(screen.getByText(/API rate limit exceeded/)).toBeTruthy();
    expect(screen.queryByText("No knowledge bases yet.")).toBeNull();
  });

  it("lets the user try again after a failure", async () => {
    vi.mocked(getAgents).mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(agents);

    render(page());
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

    await waitFor(() => expect(screen.getByText("page for kb-1")).toBeTruthy());
  });
});
