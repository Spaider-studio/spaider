/**
 * J1 and J2: what a new user does first, start to end in the browser.
 * Create a knowledge base, see its key once, add knowledge in the Studio, ask a question,
 * see which facts the answer rests on, and rate it.
 *
 * With the rig's test model the answer text is always "ok", so the content checks are on
 * the evidence and the feedback, not on the wording. The same journey run against a real
 * model (RIG_REAL_LLM=1) also checks the answer.
 */
import { expect, test } from "@playwright/test";
import { login, rig, watch } from "./rig";

const REAL = process.env.RIG_REAL_LLM === "1";
// A real model needs seconds per call and an ingest makes several; the stub answers at once.
const SLOW = REAL ? 6 : 1;

test("J1/J2: create a knowledge base, add knowledge, ask, and rate the answer", async ({ page }) => {
  test.setTimeout(240_000 * SLOW);
  const seen = watch(page);
  const name = `J1 ${Date.now()}`;
  await login(page, "owner");

  // J1: a new knowledge base, and its key, shown once.
  await page.goto("/agents");
  await page.getByRole("button", { name: "New Agent" }).click();
  await page.getByPlaceholder("e.g. research-agent-01").fill(name);
  await page.getByRole("button", { name: "Create Agent" }).click();
  // The new knowledge base is listed with a way to copy its key.
  const card = page.locator("div", { hasText: name }).filter({ has: page.getByRole("button", { name: "Copy API key" }) }).last();
  await expect(card.getByRole("button", { name: "Copy API key" })).toBeVisible();

  // The Studio starts on "all knowledge bases", where adding knowledge makes no sense. It has to say so.
  await page.goto("/studio");
  await page.waitForLoadState("networkidle");
  await expect(page.getByText(/Select a specific agent to ingest knowledge/)).toBeVisible();

  // Choose the new knowledge base in the Studio's own picker.
  await page.getByRole("button", { name: /Multiverse/ }).click();
  await page.getByRole("button", { name: name, exact: true }).click();

  // Add knowledge.
  await page.getByPlaceholder("Paste text, articles, conversation logs…").fill(
    "Mara Quist leads project Orion at Nordlicht. Project Orion uses the Atlas robot arm in Hall 7."
  );
  const ingested = page.waitForResponse((r) => r.url().includes("/ingest") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Ingest", exact: true }).click();
  expect((await ingested).status()).toBeLessThan(300);

  // The graph for that knowledge base has nodes now.
  const kbId = await page.evaluate(
    async ([key, kbName]) => {
      const list = await (await fetch("/api/v1/agents", { headers: { "X-Api-Key": key } })).json();
      const found = (list.agents ?? list).find((a: { name: string }) => a.name === kbName);
      return found?.id as string;
    },
    [rig().keys.owner, name]
  );
  await expect
    .poll(
      async () => {
        const res = await page.request.get(`/api/v1/graph/stats?agent_id=${kbId}`, { headers: { "X-Api-Key": rig().keys.owner } });
        return (await res.json()).node_count as number;
      },
      { timeout: 60_000 * SLOW }
    )
    .toBeGreaterThan(0);

  // J2: ask, see the evidence, rate the answer.
  const question = page.getByPlaceholder("Ask anything about the graph...");
  await question.fill("Who leads project Orion?");
  const asked = page.waitForResponse((r) => /\/query(\/stream)?$/.test(new URL(r.url()).pathname) && r.request().method() === "POST");
  await question.press("Enter");
  expect((await asked).status()).toBe(200);
  // With Synaptic Memory off the panel asks "Was this right?", with it on the Hebbian panel shows
  // the strength of the facts used. Either one carries the rating.
  await expect(page.getByText(/Was this right\?|Hebbian update:/)).toBeVisible({ timeout: 90_000 * SLOW });
  if (REAL) await expect(page.getByText(/Mara Quist/).first()).toBeVisible();

  const rated = page.waitForResponse((r) => r.url().includes("/system/feedback"));
  await page.getByRole("button", { name: /^(Yes|Good)$/ }).click();
  expect((await rated).status()).toBeLessThan(300);
  await expect(page.getByText(/Thanks, feedback saved\.|Synapses reinforced/)).toBeVisible();

  expect(seen.failedRequests, "failed API calls").toEqual([]);
  expect(seen.consoleErrors, "console errors").toEqual([]);
});
