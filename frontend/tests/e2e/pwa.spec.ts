import { expect, test } from "@playwright/test";
test("manifest and static offline shell remain while vehicle APIs are never cached", async ({ page, request, context }) => {
  const manifest = await request.get("/manifest.webmanifest"); expect(manifest.ok()).toBe(true); const body = await manifest.json(); expect(body.display).toBe("standalone"); expect(body.icons).toHaveLength(3);
  await page.goto("/login");
  await page.getByLabel(/用戶名稱|用户名/).fill("operator");
  await page.locator("#login-password").fill("correct horse battery staple");
  await page.getByRole("button", { name: /登入|登录/, exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.evaluate(() => navigator.serviceWorker.ready); await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller));
  await page.evaluate(() => fetch("/api/portal/vehicles?q=UY8280").then((response) => response.json()));
  expect(await page.evaluate(async () => (await caches.keys()).some((name) => name.includes("sanitized-data")))).toBe(false);
  await context.setOffline(true);
  const offline = await page.evaluate(async () => { try { await fetch("/api/portal/vehicles?q=UY8280"); return "unexpected-success"; } catch { return "network-failed"; } });
  expect(offline).toBe("network-failed");
  await context.setOffline(false);
  await page.goto("/offline"); await expect(page.getByRole("heading", { name: "目前離線" })).toBeVisible();
});
