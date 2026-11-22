/**
 * J6, J9 and J10: the Train page, per-knowledge-base settings that must survive a reload,
 * and moving around with keyboard, back/forward, deep links and reloads.
 */
import { expect, test, type Page } from "@playwright/test";
import { login, rig, watch } from "./rig";

const PAGES = ["/studio", "/multiverse", "/agents", "/synthesizer", "/members", "/sharing", "/learning", "/train", "/audit", "/settings"];

async function chooseKb(page: Page, name: string) {
  await page.getByRole("combobox", { name: "Knowledge base" }).selectOption({ label: name });
}

test("J6: the Train page says when a provider is not set up and blocks the start", async ({ page }) => {
  const seen = watch(page);
  await login(page, "owner");
  await page.goto("/train");
  await chooseKb(page, "rig-alpha");
  await expect(page.getByText("No training jobs yet.")).toBeVisible();

  const providers: { id: string; label?: string; configured: boolean; env_var: string }[] = await (
    await page.request.get("/api/v1/finetune/providers", { headers: { "X-Api-Key": rig().keys.owner } })
  ).json();
  expect(providers.length).toBeGreaterThan(0);

  const start = page.getByRole("button", { name: "Start training" });
  const model = page.getByLabel(/Model to start from/);
  const select = page.locator("form label", { hasText: "Provider" }).locator("select");
  for (const provider of providers) {
    await select.selectOption(provider.id);
    // Nothing starts without a model, whatever the provider.
    await expect(start).toBeDisabled();
    await model.fill("some-model-id");
    if (provider.configured) {
      await expect(page.getByRole("status").filter({ hasText: "is not set up" })).toHaveCount(0);
      await expect(start).toBeEnabled();
    } else {
      // The reason is shown, with the variable to set, and the start stays blocked.
      await expect(page.getByRole("status").filter({ hasText: provider.env_var })).toBeVisible();
      await expect(start).toBeDisabled();
    }
    await expect(model).toHaveValue("some-model-id");
  }
  // Changing the provider clears the model, so a model of one provider never goes to another.
  await select.selectOption(providers[0].id);
  await expect(model).toHaveValue("");

  expect(seen.failedRequests, "failed API calls").toEqual([]);
  expect(seen.consoleErrors, "console errors").toEqual([]);
});

test("J9: per-knowledge-base settings are still there after a reload", async ({ page }) => {
  test.setTimeout(120_000);
  const seen = watch(page);
  const name = `J9 ${Date.now()}`;
  await login(page, "owner");
  await page.goto("/agents");
  await page.getByRole("button", { name: "New Agent" }).click();
  await page.getByPlaceholder("e.g. research-agent-01").fill(name);
  await page.getByRole("button", { name: "Create Agent" }).click();

  const card = () =>
    page.locator("div", { hasText: name }).filter({ has: page.getByRole("button", { name: "Export Brain" }) }).last();
  await expect(card().getByRole("button", { name: "Export Brain" })).toBeVisible();

  // Turn each switch to the other side and wait for the server to confirm it.
  const memory = card().getByRole("button", { name: "Off", exact: true });
  const supersede = card().getByRole("button", { name: "Working", exact: true });
  const cadence = card().getByRole("combobox", { name: "How often this agent consolidates" });

  const memorySaved = page.waitForResponse((r) => /memory-mode|memory_mode/.test(r.url()) && r.request().method() !== "GET");
  await memory.click();
  expect((await memorySaved).status()).toBeLessThan(300);
  const supersedeSaved = page.waitForResponse((r) => /supersession/.test(r.url()) && r.request().method() !== "GET");
  await supersede.click();
  expect((await supersedeSaved).status()).toBeLessThan(300);
  const cadenceSaved = page.waitForResponse((r) => /consolidation/.test(r.url()) && r.request().method() !== "GET");
  await cadence.selectOption({ label: "Daily" });
  expect((await cadenceSaved).status()).toBeLessThan(300);

  await expect(memory).toHaveAttribute("aria-pressed", "true");
  await expect(supersede).toHaveAttribute("aria-pressed", "true");

  // A reload, and a second visit through another page, show the same values.
  for (const route of ["reload", "/settings"] as const) {
    if (route === "reload") await page.reload();
    else {
      await page.goto(route);
      await page.goto("/agents");
    }
    await expect(card().getByRole("button", { name: "Export Brain" })).toBeVisible();
    await expect(card().getByRole("button", { name: "Off", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(card().getByRole("button", { name: "Working", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(card().getByRole("combobox", { name: "How often this agent consolidates" })).toHaveValue("24");
  }

  expect(seen.failedRequests, "failed API calls").toEqual([]);
  expect(seen.consoleErrors, "console errors").toEqual([]);
});

test("J10: keyboard, back and forward, deep links and reloads on every page", async ({ page }) => {
  test.setTimeout(180_000);
  const seen = watch(page);
  await login(page, "owner");

  // The command palette opens on the shortcut, filters, moves with the arrows and Enter goes there.
  await page.goto("/studio");
  await page.waitForLoadState("networkidle"); // the shortcut is attached once the page has loaded its scripts
  await page.keyboard.press("Control+k");
  const search = page.getByPlaceholder("Search pages, nodes, actions...");
  await expect(search).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(search).toBeHidden();
  await page.keyboard.press("Control+k");
  await search.fill("Agents");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/agents$/);
  await expect(search).toBeHidden();

  // Back and forward come back to the same pages, and the Studio still has its graph.
  await page.goBack();
  await expect(page).toHaveURL(/\/studio$/);
  await page.goForward();
  await expect(page).toHaveURL(/\/agents$/);

  // The palette also finds nothing for nonsense and does not navigate on Enter.
  await page.keyboard.press("Control+k");
  await search.fill("zzzz-no-such-thing");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/agents$/);
  await page.keyboard.press("Escape");

  // Every page opens straight from its address, and still after a reload. Neither sends the user
  // back to the sign-in page or leaves the page empty.
  for (const route of PAGES) {
    await page.goto(route);
    await expect(page).toHaveURL(new RegExp(`${route}$`));
    await expect(page.getByRole("navigation").getByRole("link").first()).toBeVisible();
    await page.reload();
    await expect(page).toHaveURL(new RegExp(`${route}$`));
    await expect(page.getByRole("navigation").getByRole("link").first()).toBeVisible();
    await expect(page.getByLabel("API key", { exact: true })).toHaveCount(0); // the sign-in form
  }

  // The chosen knowledge base follows the user from page to page.
  await page.goto("/audit");
  await chooseKb(page, "rig-beta");
  await page.getByRole("navigation").getByRole("link", { name: "Learning" }).click();
  await expect(page).toHaveURL(/\/learning$/);
  await expect(page.getByRole("combobox", { name: "Knowledge base" })).toHaveValue(rig().kbs["rig-beta"]);
  // rig-beta never slept: the API answers 404 for that, by design, and the page says so.
  await expect(page.getByText("This knowledge base has not slept yet.")).toBeVisible();

  const unexpected = seen.failedRequests.filter((line) => !/^404 GET \/api\/v1\/agents\/[\w-]+\/sleep\/report$/.test(line));
  expect(unexpected, "failed API calls").toEqual([]);
  // The browser logs that expected 404 as a console error; nothing else may appear.
  expect(seen.consoleErrors.filter((line) => !line.includes("404")), "console errors").toEqual([]);
});
