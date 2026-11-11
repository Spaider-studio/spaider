import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import Markdown from "../src/renderer/components/chat/Markdown";

afterEach(cleanup);

describe("Markdown renderer", () => {
  it("never renders raw HTML", () => {
    const { container } = render(
      <Markdown content={'Hallo <script>window.pwned = 1</script> **fett** <img src="x" onerror="alert(1)">'} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("strong")?.textContent).toBe("fett");
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
  });

  it("renders code blocks with a language label and a copy button", () => {
    const { container } = render(<Markdown content={"```python\nprint('hi')\n```"} />);
    const block = container.querySelector('[data-testid="code-block"]');
    expect(block).not.toBeNull();
    expect(block?.textContent).toContain("python");
    expect(screen.getByRole("button", { name: "Code kopieren" })).toBeTruthy();
    expect(container.querySelector("pre code")?.textContent).toContain("print");
  });

  it("does not crash on unknown code languages", () => {
    const { container } = render(<Markdown content={"```notalanguage\nfoo\n```"} />);
    expect(container.querySelector("pre code")?.textContent).toContain("foo");
  });

  it("turns allowlisted links into buttons and others into plain text", () => {
    const { container } = render(
      <Markdown content={"[HF](https://huggingface.co/Qwen) und [anderswo](https://example.com/a)"} />,
    );
    expect(container.querySelector("a")).toBeNull();
    const button = screen.getByRole("button", { name: "HF" });
    expect(button.className).toContain("md-link");
    expect(container.textContent).toContain("anderswo");
    expect(container.textContent).toContain("https://example.com/a");
  });

  it("renders GFM tables", () => {
    const { container } = render(<Markdown content={"| a | b |\n| --- | --- |\n| 1 | 2 |"} />);
    expect(container.querySelector("table")).not.toBeNull();
  });
});
