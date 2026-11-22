import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/auth", () => ({ connectionTicket: vi.fn() }));

import { connectionTicket } from "@/lib/auth";
import LivePheromoneStream from "./LivePheromoneStream";

class FakeEventSource {
  static all: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeEventSource.all.push(this);
  }
  close() {
    this.closed = true;
  }
}

const wait = (ms: number) => act(async () => vi.advanceTimersByTimeAsync(ms));

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.all = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  let issued = 0;
  vi.mocked(connectionTicket).mockReset();
  vi.mocked(connectionTicket).mockImplementation(async () => `ticket-${++issued}`);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function start() {
  const view = render(<LivePheromoneStream />);
  await wait(0);
  return view;
}

describe("LivePheromoneStream", () => {
  it("opens the stream with a ticket", async () => {
    await start();

    expect(FakeEventSource.all).toHaveLength(1);
    expect(FakeEventSource.all[0].url).toContain("ticket=ticket-1");
  });

  it("reconnects with a new ticket after the connection drops", async () => {
    // A ticket works once. Letting the browser retry the same URL gets a 401, and
    // EventSource does not retry after that, so the feed stayed dead until a reload.
    await start();

    act(() => FakeEventSource.all[0].onerror?.());
    await wait(1000);

    expect(FakeEventSource.all).toHaveLength(2);
    expect(FakeEventSource.all[0].closed).toBe(true);
    expect(FakeEventSource.all[1].url).toContain("ticket=ticket-2");
  });

  it("waits longer after each failed attempt, up to a limit", async () => {
    await start();
    const attempts = () => FakeEventSource.all.length;

    act(() => FakeEventSource.all[0].onerror?.());
    await wait(999);
    expect(attempts()).toBe(1);
    await wait(1);
    expect(attempts()).toBe(2);

    act(() => FakeEventSource.all[1].onerror?.());
    await wait(1999);
    expect(attempts()).toBe(2);
    await wait(1);
    expect(attempts()).toBe(3);

    for (let round = 0; round < 8; round++) {
      act(() => FakeEventSource.all[FakeEventSource.all.length - 1].onerror?.());
      await wait(30_000);
    }
    const before = attempts();
    act(() => FakeEventSource.all[FakeEventSource.all.length - 1].onerror?.());
    await wait(30_000);
    expect(attempts()).toBe(before + 1);
  });

  it("starts over with a short wait once a connection works again", async () => {
    await start();
    act(() => FakeEventSource.all[0].onerror?.());
    await wait(1000);
    act(() => FakeEventSource.all[1].onerror?.());
    await wait(2000);

    act(() => FakeEventSource.all[2].onopen?.());
    act(() => FakeEventSource.all[2].onerror?.());
    await wait(1000);

    expect(FakeEventSource.all).toHaveLength(4);
  });

  it("tries again when the ticket request itself fails", async () => {
    vi.mocked(connectionTicket).mockRejectedValueOnce(new Error("Ticket request failed: 503"));

    await start();
    expect(FakeEventSource.all).toHaveLength(0);
    await wait(1000);

    expect(FakeEventSource.all).toHaveLength(1);
  });

  it("says whether it is connected", async () => {
    await start();
    expect(screen.getByText("Connecting…")).toBeTruthy();

    act(() => FakeEventSource.all[0].onopen?.());

    expect(screen.getByText("Awaiting swarm events…")).toBeTruthy();
  });

  it("labels an ACT-R activation event, spelled with plain letters", async () => {
    // The type used to be compared against a word with a Cyrillic letter in it,
    // which no event ever matched.
    await start();
    act(() => FakeEventSource.all[0].onopen?.());

    act(() => FakeEventSource.all[0].onmessage?.({ data: JSON.stringify({ type: "actr", message: "boosted" }) }));

    expect(screen.getByText("[ACT]", { exact: false })).toBeTruthy();
  });

  it("stops trying once the component is gone", async () => {
    const { unmount } = await start();
    act(() => FakeEventSource.all[0].onerror?.());

    unmount();
    await wait(60_000);

    expect(FakeEventSource.all).toHaveLength(1);
  });
});
