/**
 * Journeys that cross several elements: do the pieces work together, not just alone?
 * Each one creates its own data with a unique name, so reruns do not collide.
 */
import { expect, test, type Page } from "@playwright/test";
import { login, rig, watch } from "./rig";

const kbPicker = (page: Page) => page.getByLabel("Knowledge base", { exact: true });

async function openFor(page: Page, route: string, kbName: string) {
  await page.goto(route);
  await kbPicker(page).selectOption({ label: kbName });
  await page.waitForLoadState("networkidle");
}

test("J4: add a person, see the key once, change the role, and remove them", async ({ page, browser }) => {
  const seen = watch(page);
  const person = `J4 Person ${Date.now()}`;
  await login(page, "owner");
  await openFor(page, "/members", "rig-alpha");

  await page.getByLabel("A new person").check();
  await page.getByLabel("Name", { exact: true }).fill(person);
  await page.locator("form label", { hasText: /^Role/ }).locator("select").selectOption({ label: "Reader" });
  await page.getByRole("button", { name: "Add", exact: true }).click();

  // The key is shown once, with a way to copy it, and the person is in the list.
  const notice = page.getByRole("status").filter({ hasText: "Copy it now" });
  await expect(notice).toBeVisible();
  const key = (await notice.locator("code").innerText()).trim();
  expect(key).toMatch(/^sk-/);
  await expect(page.getByRole("listitem").filter({ hasText: person })).toBeVisible();
  await notice.getByRole("button", { name: "Done" }).click();
  await expect(notice).toHaveCount(0);

  // The new person signs in with that key, in a second browser, and sees the knowledge base.
  const other = await (await browser.newContext()).newPage();
  const otherSeen = watch(other);
  await other.goto("/login");
  await other.getByLabel("API key").fill(key);
  await other.getByRole("button", { name: "Sign in" }).click();
  await expect(other).toHaveURL(/\/studio/);
  await other.goto("/members");
  await other.waitForLoadState("networkidle");
  await expect(other.locator('a[href="/studio"]').first()).toBeVisible();

  // The owner raises the role. The list keeps the person and shows the new role.
  const roleSelect = page.getByLabel(`Role for ${person}`);
  await roleSelect.selectOption({ label: "Steward" });
  await page.reload();
  await kbPicker(page).selectOption({ label: "rig-alpha" });
  await expect(page.getByLabel(`Role for ${person}`)).toHaveValue("steward");

  // Removing them takes them out of the list.
  await page.getByRole("listitem").filter({ hasText: person }).getByRole("button", { name: "Remove" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: person })).toHaveCount(0);

  expect(seen.consoleErrors).toEqual([]);
  expect(otherSeen.consoleErrors.filter((line) => !line.startsWith("Failed to load resource"))).toEqual([]);
  await other.context().close();
});

test("J5: ask to read another knowledge base, approve it, then take it back", async ({ page }) => {
  const seen = watch(page);
  await login(page, "owner");
  const reader = "rig-with-a-very-long-name-that-should-wrap-or-truncate-in-every-table-and-dropdown";

  // Start from nothing: end any grant a run that was cut short left behind.
  const { keys, kbs } = rig();
  const headers = { "X-Api-Key": keys.owner };
  const leftovers = (await (await page.request.get("/api/v1/federation/grants", { headers })).json()) as {
    id: string;
    status: string;
    source_kb: string;
    target_kb: string;
  }[];
  for (const grant of leftovers) {
    const mine = grant.target_kb === kbs["rig-alpha"] && grant.source_kb === kbs[reader];
    if (mine && (grant.status === "requested" || grant.status === "approved")) {
      await page.request.delete(`/api/v1/federation/grants/${grant.id}`, { headers });
    }
  }

  await openFor(page, "/sharing", "rig-alpha");

  await page.getByLabel("Let rig-alpha read from").selectOption({ label: reader });
  await page.getByRole("button", { name: "Request access" }).click();

  // Earlier runs leave ended grants in the list, so find the row by what can be done to it.
  const rows = page.getByRole("listitem").filter({ hasText: reader });
  const waiting = rows.filter({ has: page.getByRole("button", { name: "Approve" }) });
  await expect(waiting).toContainText("waiting for a steward");
  await waiting.getByRole("button", { name: "Approve" }).click();

  const active = rows.filter({ has: page.getByRole("button", { name: "Revoke" }) });
  await expect(active).toContainText("active");
  await active.getByRole("button", { name: "Revoke" }).click();
  await expect(active).toHaveCount(0);
  expect(seen.consoleErrors).toEqual([]);
});

test("J5b: a question over the knowledge base returns the nodes it used", async ({ page }) => {
  // rig-alpha reads rig-beta (granted by the seed). The audit log shows the question.
  await login(page, "owner");
  const keys = rig();
  const answer = await page.request.post("/api/v1/swarm/query", {
    headers: { "X-Api-Key": keys.keys.owner },
    data: { query: "Who is the CEO of Zephyr Robotics?", agent_ids: [keys.kbs["rig-alpha"]] },
  });
  expect(answer.status()).toBe(200);
  const body = await answer.json();
  // The test model only ever answers "ok", so check what the answer was built from.
  expect(body.source_node_ids.length).toBeGreaterThan(0);
});
