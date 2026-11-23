/**
 * Every page in the states a real user ends up in: working, the API failing, the
 * network gone, the key no longer valid, and each role. The question is the same
 * each time: does the user still get a page they can understand, or a blank one?
 *
 * "Does not crash" means no uncaught exception in the page and the navigation still
 * there. The browser's own "Failed to load resource" lines for 4xx and 5xx answers are
 * expected in these states and are not counted.
 */
import { expect, test, type Page } from "@playwright/test";
import { login } from "./rig";

const PAGES = [
  "/studio",
  "/multiverse",
  "/agents",
  "/synthesizer",
  "/members",
  "/sharing",
  "/learning",
  "/train",
  "/audit",
  "/settings",
];

/** Exceptions the page did not catch, and React's own error reports. */
function crashes(page: Page): string[] {
  const seen: string[] = [];
  page.on("pageerror", (error) => seen.push(`uncaught: ${error.message}`));
  page.on("console", (message) => {
    const text = message.text();
    if (message.type() === "error" && !text.startsWith("Failed to load resource") && /error occurred|Uncaught|is not a function|undefined/i.test(text)) {
      seen.push(`console: ${text.slice(0, 160)}`);
    }
  });
  return seen;
}

async function stillHasNavigation(page: Page) {
  await expect(page.locator('a[href="/studio"]').first()).toBeVisible({ timeout: 15_000 });
}

for (const route of PAGES) {
  test.describe(route, () => {
    test("works for the owner without console errors", async ({ page }) => {
      const seen: string[] = [];
      page.on("pageerror", (error) => seen.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error") seen.push(message.text().slice(0, 160));
      });
      await login(page, "owner");

      await page.goto(route);
      await page.waitForLoadState("networkidle");

      await stillHasNavigation(page);
      expect(seen).toEqual([]);
    });

    test("survives the API answering 500", async ({ page }) => {
      await login(page, "owner");
      const seen = crashes(page);
      await page.route(/\/api\/v1\//, (r) =>
        r.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "boom" }) })
      );

      await page.goto(route);
      await page.waitForLoadState("networkidle");

      await stillHasNavigation(page);
      expect(seen).toEqual([]);
    });

    test("survives the network failing", async ({ page }) => {
      await login(page, "owner");
      const seen = crashes(page);
      await page.route(/\/api\/v1\//, (r) => r.abort("connectionfailed"));

      await page.goto(route);
      await page.waitForLoadState("networkidle");

      await stillHasNavigation(page);
      expect(seen).toEqual([]);
    });

    test("goes to the sign-in page when the key stops working", async ({ page }) => {
      await login(page, "owner");
      await page.route(/\/api\/v1\//, (r) =>
        r.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Invalid key" }) })
      );

      await page.goto(route);

      await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
    });
  });
}

for (const role of ["reader", "contributor", "editor", "steward", "auditor"]) {
  test.describe(`as ${role}`, () => {
    for (const route of ["/members", "/sharing", "/learning", "/train", "/audit", "/agents", "/settings"]) {
      test(`${route} does not crash`, async ({ page }) => {
        await login(page, role);
        const seen = crashes(page);

        await page.goto(route);
        await page.waitForLoadState("networkidle");

        await stillHasNavigation(page);
        expect(seen).toEqual([]);
      });
    }
  });
}
