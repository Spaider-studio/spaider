/**
 * Accessibility (axe) and layout at the widths people really use.
 *
 * axe findings of "serious" or "critical" impact fail the page they are on. KNOWN can
 * record a number of allowed kinds per page, so a finding that cannot be fixed yet stays
 * visible, but it starts empty.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login } from "./rig";

const PAGES = ["/studio", "/multiverse", "/agents", "/synthesizer", "/members", "/sharing", "/learning", "/train", "/audit", "/settings"];

// Findings recorded when the check was added. Fix the cause, then lower the number.
// Kinds of serious or critical finding allowed per page. Nothing is allowed: every page was
// brought to zero (low contrast, unnamed buttons, sliders and selects). A new finding fails.
const KNOWN: Record<string, number> = {};

for (const route of PAGES) {
  test(`${route} has no serious accessibility findings beyond the recorded ones`, async ({ page }) => {
    await login(page, "owner");
    await page.goto(route);
    await page.waitForLoadState("networkidle");

    const result = await new AxeBuilder({ page } as never).analyze();
    const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    console.log(`AXE ${route}: ${serious.map((v) => `${v.id}(${v.nodes.length})`).join(", ") || "none"}`);

    expect(serious.length, serious.map((v) => v.id).join(", ")).toBeLessThanOrEqual(KNOWN[route] ?? 0);
  });
}

for (const width of [1440, 1024, 768, 375]) {
  test.describe(`at ${width}px`, () => {
    test.use({ viewport: { width, height: 800 } });

    for (const route of ["/agents", "/members", "/sharing", "/audit", "/settings", "/train"]) {
      test(`${route} does not scroll sideways`, async ({ page }) => {
        await login(page, "owner");
        await page.goto(route);
        await page.waitForLoadState("networkidle");

        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow, "page is wider than the window by this many pixels").toBeLessThanOrEqual(1);
      });
    }
  });
}

test("a long knowledge base name does not break the header or the list", async ({ page }) => {
  await login(page, "owner");
  await page.goto("/members");
  await page
    .getByLabel("Knowledge base", { exact: true })
    .selectOption({ label: "rig-with-a-very-long-name-that-should-wrap-or-truncate-in-every-table-and-dropdown" });
  await page.waitForLoadState("networkidle");

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
