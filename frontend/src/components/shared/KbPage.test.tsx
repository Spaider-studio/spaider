import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ getAgents: vi.fn() }));
vi.mock("@/components/layout/DashboardShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import { getAgents } from "@/lib/api";
import KbPage from "./KbPage";

const agents = [
  { id: "kb-1", name: "Alpha" },
  { id: "kb-2", name: "Beta" },
] as never;

beforeEach(() => {
  // Braces matter: a function returned from beforeEach is called as cleanup.
  vi.mocked(getAgents).mockReset();
});

describe("KbPage", () => {
  it("shows the page for the first knowledge base", async () => {
    vi.mocked(getAgents).mockResolvedValue(agents);

    render(<KbPage title="Members">{(id) => <p>page for {id}</p>}</KbPage>);

    expect(await screen.findByText("page for kb-1")).toBeTruthy();
  });

  it("switches the page when another knowledge base is chosen", async () => {
    vi.mocked(getAgents).mockResolvedValue(agents);
    const { container } = render(<KbPage title="Members">{(id) => <p>page for {id}</p>}</KbPage>);
    await screen.findByText("page for kb-1");

    fireEvent.change(container.querySelector("select")!, { target: { value: "kb-2" } });

    expect(screen.getByText("page for kb-2")).toBeTruthy();
  });

  it("says so when there really are no knowledge bases", async () => {
    vi.mocked(getAgents).mockResolvedValue([]);

    render(<KbPage title="Members">{(id) => <p>page for {id}</p>}</KbPage>);

    expect(await screen.findByText("No knowledge bases yet.")).toBeTruthy();
  });

  it("says the list could not be loaded, instead of claiming there are none", async () => {
    // A rate limit or a server error used to look exactly like an empty install.
    vi.mocked(getAgents).mockRejectedValue(new Error("API rate limit exceeded"));

    render(<KbPage title="Members">{(id) => <p>page for {id}</p>}</KbPage>);

    expect(await screen.findByText(/Could not load your knowledge bases/)).toBeTruthy();
    expect(screen.getByText(/API rate limit exceeded/)).toBeTruthy();
    expect(screen.queryByText("No knowledge bases yet.")).toBeNull();
  });

  it("lets the user try again after a failure", async () => {
    vi.mocked(getAgents).mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(agents);

    render(<KbPage title="Members">{(id) => <p>page for {id}</p>}</KbPage>);
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

    await waitFor(() => expect(screen.getByText("page for kb-1")).toBeTruthy());
  });
});
