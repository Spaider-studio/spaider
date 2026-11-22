import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("./CommandPalette", () => ({
  default: ({ open }: { open: boolean }) => (open ? <div>palette is open</div> : null),
}));

import CommandPaletteHost from "./CommandPaletteHost";

describe("CommandPaletteHost", () => {
  it("opens and closes the palette with Ctrl+K", () => {
    // The Studio and the Multiverse have their own top bar, so the shortcut that the shared
    // header carries was missing on the page people land on first.
    render(<CommandPaletteHost />);
    expect(screen.queryByText("palette is open")).toBeNull();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByText("palette is open")).toBeTruthy();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.queryByText("palette is open")).toBeNull();
  });

  it("opens with Cmd+K and ignores a plain k", () => {
    render(<CommandPaletteHost />);
    fireEvent.keyDown(window, { key: "k" });
    expect(screen.queryByText("palette is open")).toBeNull();

    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByText("palette is open")).toBeTruthy();
  });

  it("stops listening when it is removed", () => {
    const { unmount } = render(<CommandPaletteHost />);
    const remove = vi.spyOn(window, "removeEventListener");
    unmount();
    expect(remove).toHaveBeenCalledWith("keydown", expect.any(Function));
  });
});
