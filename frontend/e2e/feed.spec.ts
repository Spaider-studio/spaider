import { expect, test } from "@playwright/test";
import { login, rigDocker } from "./rig";

test("the live feed comes back by itself after the API restarts", async ({ page }) => {
  test.setTimeout(150_000);
  await login(page, "owner");
  await page.goto("/studio");
  await expect(page.getByText("Awaiting swarm events…")).toBeVisible({ timeout: 20_000 });

  rigDocker("restart", "spaider-backend-api");

  // The stream drops, then reconnects with a new ticket, with no reload.
  await expect(page.getByText("Connecting…")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Awaiting swarm events…")).toBeVisible({ timeout: 90_000 });
});
