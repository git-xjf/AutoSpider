import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Root route", () => {
  test("opens the workspace directly", async ({ page }) => {
    mockLangGraphAPI(page);

    await page.goto("/");
    await page.waitForURL("**/workspace/chats/new");
  await expect(page).toHaveURL(
    /\/workspace\/agents\/crawler-lead\/chats\/new/,
  );
  });
});
