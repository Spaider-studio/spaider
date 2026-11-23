import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/members" }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import Sidebar from "./Sidebar";

describe("Sidebar collapse button", () => {
  it("has a name, so a screen reader can tell what it does", () => {
    // It was a round icon-only button with no text, on every page of the Studio.
    render(<Sidebar />);

    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeTruthy();
  });

  it("says which way it will go, and whether the sidebar is open", () => {
    render(<Sidebar />);
    const toggle = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(toggle);

    const opened = screen.getByRole("button", { name: "Expand sidebar" });
    expect(opened.getAttribute("aria-expanded")).toBe("false");
  });
});
