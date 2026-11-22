import { expect, test, type Page } from "@playwright/test";
import { login, rig } from "./rig";

const LIST = /\/api\/v1\/replay\/workflows(\?|$)/;

function run(id: string, kb: string) {
  return {
    workflow_id: id,
    agent_id: kb,
    first_event: "2026-01-01T00:00:00+00:00",
    last_event: "2026-01-01T00:01:00+00:00",
    event_count: 2,
  };
}

/** The one place to choose a knowledge base: the picker in the header. */
function kbPicker(page: Page) {
  return page.getByLabel("Knowledge base", { exact: true });
}

function countListRequests(page: Page): () => number {
  let count = 0;
  page.on("request", (request) => {
    if (request.method() === "GET" && new URL(request.url()).pathname === "/api/v1/replay/workflows") count += 1;
  });
  return () => count;
}

test.beforeEach(async ({ page }) => {
  await login(page, "owner");
});

test("opening the audit log asks for the run list once", async ({ page }) => {
  const asked = countListRequests(page);

  await page.goto("/audit");
  await page.waitForLoadState("networkidle");

  expect(asked()).toBe(1);
});

test("filtering by KB shows that KB's runs", async ({ page }) => {
  const asked = countListRequests(page);
  await page.goto("/audit");
  await page.waitForLoadState("networkidle");

  await kbPicker(page).selectOption({ label: "rig-alpha" });
  await page.waitForLoadState("networkidle");

  expect(asked()).toBe(2);
  // Each run row names its KB (not the first eight characters of its id).
  await expect(page.getByRole("button", { name: /rig-alpha.*\d+ events/ }).first()).toBeVisible();
});

test("the audit log follows the knowledge base chosen in the header, even when it was chosen on another page", async ({ page }) => {
  await page.goto("/members");
  await kbPicker(page).selectOption({ label: "rig-beta" });
  const asked: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/replay/workflows") asked.push(url.searchParams.get("agent_id") ?? "all");
  });

  await page.locator('a[href="/audit"]').click();
  await page.waitForLoadState("networkidle");

  expect(asked).toEqual([rig().kbs["rig-beta"]]);
  await expect(kbPicker(page)).toHaveValue(rig().kbs["rig-beta"]);
});

test("a partial answer says so", async ({ page }) => {
  const kb = rig().kbs["rig-alpha"];
  await page.route(LIST, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ workflows: [run("only-recent", kb)], total: 1, source: "kafka", complete: false }),
    })
  );

  await page.goto("/audit");

  await expect(page.getByRole("status")).toContainText("only recent runs");
  await expect(page.getByText("only-recent")).toBeVisible();
});

test("an unavailable audit store is an error, not an empty log", async ({ page }) => {
  await page.route(LIST, (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "The audit store is unavailable. Try again shortly." }),
    })
  );

  await page.goto("/audit");

  await expect(page.getByText("The audit store is unavailable. Try again shortly.")).toBeVisible();
  await expect(page.getByText("No workflow runs yet")).toHaveCount(0);
  await expect(page.getByText("The run list could not be loaded")).toBeVisible();
});

test("switching KB while the first list is still loading never shows the older answer", async ({ page }) => {
  const kb = rig().kbs["rig-alpha"];
  await page.route(LIST, async (route) => {
    const filtered = new URL(route.request().url()).searchParams.has("agent_id");
    if (!filtered) await new Promise((resolve) => setTimeout(resolve, 2500));
    const name = filtered ? "newer-answer" : "older-answer";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ workflows: [run(name, kb)], total: 1 }),
    });
  });

  await page.goto("/audit");
  await kbPicker(page).selectOption({ label: "rig-alpha" });
  await expect(page.getByText("newer-answer")).toBeVisible();
  await page.waitForTimeout(3500); // the older, slower answer has arrived by now

  await expect(page.getByText("newer-answer")).toBeVisible();
  await expect(page.getByText("older-answer")).toHaveCount(0);
});
