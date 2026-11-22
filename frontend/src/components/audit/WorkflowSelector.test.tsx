import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getAgents: vi.fn(async () => [
    { id: "kb-1", name: "Alpha" },
    { id: "kb-2", name: "Beta" },
  ]),
}));

import WorkflowSelector from "./WorkflowSelector";

const props = { runs: [], selectedRun: null, loading: false, onSelectRun: vi.fn() };

beforeEach(() => vi.clearAllMocks());

describe("WorkflowSelector", () => {
  it("does not load anything by itself when it appears", async () => {
    const onFilterChange = vi.fn();
    const { container } = render(<WorkflowSelector {...props} onFilterChange={onFilterChange} />);
    await waitFor(() => expect(container.querySelectorAll("option").length).toBeGreaterThan(1));

    expect(onFilterChange).not.toHaveBeenCalled();
  });

  it("asks for the runs of the chosen KB when the user picks one", async () => {
    const onFilterChange = vi.fn();
    const { container } = render(<WorkflowSelector {...props} onFilterChange={onFilterChange} />);
    await waitFor(() => expect(container.querySelectorAll("option").length).toBeGreaterThan(1));

    fireEvent.change(container.querySelector("select")!, { target: { value: "kb-2" } });

    expect(onFilterChange).toHaveBeenCalledTimes(1);
    expect(onFilterChange).toHaveBeenCalledWith("kb-2", undefined);
  });

  it("goes back to all KBs when the filter is cleared", async () => {
    const onFilterChange = vi.fn();
    const { container } = render(<WorkflowSelector {...props} onFilterChange={onFilterChange} />);
    await waitFor(() => expect(container.querySelectorAll("option").length).toBeGreaterThan(1));
    const select = container.querySelector("select")!;

    fireEvent.change(select, { target: { value: "kb-2" } });
    fireEvent.change(select, { target: { value: "" } });

    expect(onFilterChange).toHaveBeenLastCalledWith(undefined, undefined);
  });
});
