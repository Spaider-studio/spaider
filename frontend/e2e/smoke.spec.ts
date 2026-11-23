import { expect, test } from "@playwright/test";
import { login, watch } from "./rig";

test("a wrong key is rejected with a message", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("API key").fill("sk-not-a-real-key");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("That key was not accepted.")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("the owner signs in and every main page loads without errors", async ({ page }, info) => {
  const seen = watch(page);
  await login(page, "owner");
  const counts: string[] = [];
  for (const route of ["/studio", "/agents", "/audit", "/members", "/sharing", "/settings"]) {
    const before = seen.apiRequests.length;
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    await expect(page).toHaveURL(new RegExp(`${route}$`));
    counts.push(`${route}: ${seen.apiRequests.length - before} API requests`);
  }
  info.annotations.push({ type: "api requests per page", description: counts.join("; ") });
  console.log(counts.join("\n"));
  expect(seen.failedRequests, "unexpected failed API requests").toEqual([]);
  expect(seen.consoleErrors, "console errors").toEqual([]);
});
