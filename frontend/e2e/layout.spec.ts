/**
 * Accessibility (axe) and layout at the widths people really use.
 *
 * axe findings of "serious" or "critical" impact are reported per page. The test
 * fails when a page has more than the number recorded in KNOWN, so existing
 * findings are visible and can only go down, and a new one fails the build.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login } from "./rig";

const PAGES = ["/studio", "/multiverse", "/agents", "/synthesizer", "/members", "/sharing", "/learning", "/train", "/audit", "/settings"];

// Findings recorded when the check was added. Fix the cause, then lower the number.
// Kinds of finding per page, measured on the rig. Almost all of it is colour contrast,
// a design change that is the user's call. The rest are controls without a name
// (sliders and selects on the synthesizer page, one button each on Studio and Agents).
const KNOWN: Record<string, number> = {
  "/studio": 2,
  "/multiverse": 1,
  "/agents": 3,
  "/synthesizer": 3,
  "/members": 1,
  "/sharing": 1,
  "/learning": 1,
  "/train": 1,
  "/audit": 1,
  "/settings": 1,
};

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
