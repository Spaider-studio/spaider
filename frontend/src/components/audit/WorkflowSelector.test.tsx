import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getAgents: vi.fn(async () => [
    { id: "kb-1", name: "Alpha" },
    { id: "kb-2", name: "Beta" },
  ]),
  getGraph: vi.fn(),
  getGraphClusters: vi.fn(),
  getGraphStats: vi.fn(),
  getMultiverseGraph: vi.fn(),
}));

import { useGraph } from "@/hooks/useGraph";
import type { WorkflowRun } from "@/lib/types";
import WorkflowSelector from "./WorkflowSelector";

function run(id: string, kb: string): WorkflowRun {
  return { id, workflow_id: id, agent_id: kb, status: "completed", started_at: "2026-01-01T00:00:00Z", event_count: 3 };
}

const props = { selectedRun: null, loading: false, onSelectRun: vi.fn() };

beforeEach(() => {
  vi.clearAllMocks();
  useGraph.setState({
    agents: [
      { id: "kb-1", name: "Alpha" },
      { id: "kb-2", name: "Beta" },
    ] as never,
  });
});

describe("WorkflowSelector", () => {
  it("has no knowledge base picker of its own: the header's choice applies", () => {
    // The audit page used to have a second picker with a different meaning next to the header's.
    const { container } = render(<WorkflowSelector {...props} runs={[]} />);

    expect(container.querySelector("select")).toBeNull();
  });

  it("names the knowledge base of each run, not the start of its id", () => {
    render(<WorkflowSelector {...props} runs={[run("run-a", "kb-1"), run("run-b", "kb-2")]} />);

    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
  });

  it("falls back to the start of the id for a knowledge base it does not know", () => {
    render(<WorkflowSelector {...props} runs={[run("run-a", "d5f139aa-a873-4b29")]} />);

    expect(screen.getByText("d5f139aa")).toBeTruthy();
  });

  it("filters the list by what is typed", () => {
    render(<WorkflowSelector {...props} runs={[run("alpha-run", "kb-1"), run("beta-run", "kb-2")]} />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "beta" } });

    expect(screen.queryByText("alpha-run")).toBeNull();
    expect(screen.getByText("beta-run")).toBeTruthy();
  });

  it("says there are no runs yet only when that is true", () => {
    render(<WorkflowSelector {...props} runs={[]} />);

    expect(screen.getByText(/No workflow runs yet/)).toBeTruthy();
  });

  it("does not claim there are no runs when loading them failed", () => {
    render(<WorkflowSelector {...props} runs={[]} failed />);

    expect(screen.queryByText(/No workflow runs yet/)).toBeNull();
    expect(screen.getByText(/could not be loaded/)).toBeTruthy();
  });
});
