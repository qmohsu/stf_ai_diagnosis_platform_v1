import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const workshopId = "11111111-1111-4111-8111-111111111111";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("用戶名稱").fill("manager");
  await page.getByLabel("密碼", { exact: true }).fill("valid-password");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的工作坊" })).toBeVisible();
}

test("login, workshop pagination, search and vehicle detail work on this viewport", async ({ page }) => {
  const browserRequests: string[] = [];
  page.on("request", (request) => browserRequests.push(request.url()));
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await login(page);
  await page.goto("/login");
  await expect(page).toHaveURL("/");
  await expect(page.getByRole("heading", { name: "我的工作坊" })).toBeVisible();
  await page.getByRole("link", { name: /測試工作坊/ }).click();
  await expect(page).toHaveURL(new RegExp(`/workshops/${workshopId}$`));
  await expect(page.locator(".vehicle-card")).toHaveCount(50);
  await page.getByRole("button", { name: "下一頁" }).click();
  await expect(page.locator(".vehicle-card")).toHaveCount(1);
  await page.getByPlaceholder("搜尋 VIN、車牌或暱稱").fill("STF-001");
  await expect(page.locator(".vehicle-card")).toHaveCount(1);
  await page.getByRole("link", { name: /STF-001/ }).click();
  await expect(page).toHaveURL(/\/vehicles\/00000000-0000-4000-8000-000000000001$/);
  await expect(page.getByRole("heading", { level: 1, name: "測試車輛" })).toBeVisible();
  await expect(page.getByText("JHMGK5830HX202404")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("secret_internal");
  const pageOrigin = new URL(page.url()).origin;
  const apiRequests = browserRequests.map((url) => new URL(url)).filter(({ pathname }) => pathname.startsWith("/api/"));
  expect(apiRequests.some(({ pathname }) => pathname === "/api/auth/login")).toBe(true);
  expect(apiRequests.some(({ pathname }) => pathname.startsWith("/api/portal/"))).toBe(true);
  expect(browserRequests.filter((url) => {
    const requested = new URL(url);
    return requested.origin !== pageOrigin || requested.pathname.startsWith("/v3/");
  })).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("invalid login and sign-out show the correct state", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用戶名稱").fill("manager");
  await page.getByLabel("密碼", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.locator(".login-error")).toContainText("不正確");
  await page.getByLabel("密碼", { exact: true }).fill("valid-password");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的工作坊" })).toBeVisible();
  let rejectedLogoutRequests = 0;
  await page.route("**/api/auth/logout", async (route) => { rejectedLogoutRequests += 1; await route.fulfill({ status: 503, body: "unavailable" }); });
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.locator(".logout-error")).toContainText("登出失敗");
  expect(rejectedLogoutRequests).toBe(1);
  await expect(page.getByRole("heading", { name: "我的工作坊" })).toBeVisible();
  await page.unroute("**/api/auth/logout");
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
});

test("old vehicle links return to the workshop entry and private responses are not cached", async ({ page, context }) => {
  await login(page);
  await context.addCookies([{ name: "portal_vehicle_context", value: "old-context", url: "http://127.0.0.1:3001/" }]);
  await page.goto("/vehicles/plate/STF-001");
  await expect(page).toHaveURL(/\/$/);
  expect((await context.cookies()).some((cookie) => cookie.name === "portal_vehicle_context")).toBe(false);
  await page.goto("/vehicles/vin/JHMGK5830HX202404/components/engine");
  await expect(page).toHaveURL(/\/$/);
  await page.getByRole("link", { name: /測試工作坊/ }).click();
  await expect(page.locator(".vehicle-card").first()).toBeVisible();
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  const cachedUrls = await page.evaluate(async () => (await Promise.all((await caches.keys()).map(async (name) => (await (await caches.open(name)).keys()).map((item) => item.url)))).flat());
  expect(cachedUrls.some((url) => url.includes("/api/portal/") || url.includes("/api/auth/"))).toBe(false);
  const offlineHtml = await page.evaluate(async () => (await (await caches.match("/offline.html"))?.text()) ?? "");
  expect(offlineHtml).toContain("目前離線");
  expect(offlineHtml).not.toContain("manager");
  expect(offlineHtml).not.toContain("JHMGK5830HX202404");
  await context.setOffline(true);
  await expect(page.getByRole("heading", { name: "目前離線" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("STF-001");
  await expect(page.locator("body")).not.toContainText("manager");
  await page.reload();
  await expect(page.getByRole("heading", { name: "目前離線" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("manager");
});

test("one-click diagnosis resumes after refresh and a report citation opens the V3 manual source", async ({ page, context }) => {
  const browserRequests: string[] = [];
  page.on("request", (request) => browserRequests.push(request.url()));
  await login(page);
  await page.getByRole("link", { name: /測試工作坊/ }).click();
  await page.getByRole("link", { name: /STF-001/ }).click();
  await expect(page.getByRole("heading", { name: "行車日誌" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "以往診斷" })).toBeVisible();
  await expect(page.getByText("first-drive.csv")).toBeVisible();
  await expect(page.getByText("網頁上傳")).toBeVisible();
  await page.getByRole("button", { name: "診斷日誌 first-drive.csv" }).click();
  await expect(page).toHaveURL(/\/vehicles\/00000000-0000-4000-8000-000000000001\/conversations\/77777777-7777-4777-8777-777777777777$/);
  await expect(page.getByRole("heading", { level: 1, name: "診斷過程" })).toBeVisible();
  await expect(page.locator(".event-item").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.reload();
  await expect(page.locator(".event-item").first()).toBeVisible();
  await page.getByRole("tab", { name: "技術細節" }).click();
  await expect(page.getByRole("tab", { name: "技術細節" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("已查閱維修手冊，請檢查電裝系統。")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "引用與證據" })).toBeVisible();
  await expect(page.getByText("未經診斷過程核實")).toBeVisible();
  await page.getByRole("link", { name: /查看手冊原文/ }).click();
  await expect(page).toHaveURL(/\/manuals\/33333333-3333-4333-8333-333333333333\?section=/);
  await expect(page.locator(".page-heading").getByRole("heading", { level: 1, name: "電裝系統" })).toBeVisible();
  await expect(page.getByText("原文：檢查電路接頭與保險絲。")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  const manualImage = page.getByRole("img", { name: "電路示意圖" });
  await expect(manualImage).toBeVisible();
  await expect.poll(() => manualImage.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);

  const pageOrigin = new URL(page.url()).origin;
  expect(browserRequests.filter((url) => {
    const requested = new URL(url);
    return requested.origin !== pageOrigin || requested.pathname.startsWith("/v3/");
  })).toEqual([]);
  const cachedUrls = await page.evaluate(async () => (await Promise.all((await caches.keys()).map(async (name) => (await (await caches.open(name)).keys()).map((item) => item.url)))).flat());
  expect(cachedUrls.some((url) => url.includes("/api/portal/") || url.includes("/api/auth/"))).toBe(false);
  await context.setOffline(true);
  await expect(page.getByRole("heading", { name: "目前離線" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("檢查電路接頭與保險絲");
});

test("an already-running diagnosis of another log is explained before navigation", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: /測試工作坊/ }).click();
  await page.getByRole("link", { name: /STF-001/ }).click();
  await page.route("**/api/portal/vehicles/*/diagnose", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      conversation_id: "77777777-7777-4777-8777-777777777777",
      status: "running", existing: true,
      obd_log_id: "55555555-5555-4555-8555-555555555555",
    }) });
  });
  await page.getByRole("button", { name: "診斷日誌 first-drive.csv" }).click();
  await expect(page).toHaveURL(/\/vehicles\/00000000-0000-4000-8000-000000000001$/);
  await expect(page.getByText("已有另一筆日誌的診斷進行中，本次未建立新診斷。")).toBeVisible();
  await expect(page.getByRole("link", { name: "繼續查看既有診斷" })).toBeVisible();
});

test("invite registration, vehicle creation, upload and manager device setup stay on the V3 portal", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.goto("/register");
  await page.getByLabel("用戶名稱").fill("new.technician");
  await page.getByLabel("密碼（至少 10 字元）").fill("strong-password");
  await page.getByLabel("邀請碼").fill("TEST-INVITE");
  await page.getByRole("button", { name: "註冊", exact: true }).click();
  await expect(page.getByText("註冊成功。")).toBeVisible();
  await page.getByRole("link", { name: "前往登入" }).click();
  await page.getByLabel("用戶名稱").fill("manager");
  await page.getByLabel("密碼", { exact: true }).fill("valid-password");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await page.getByRole("link", { name: /測試工作坊/ }).click();
  await page.getByRole("button", { name: "建立車檔" }).click();
  await page.getByLabel("VIN（17 碼）").fill("JHMGK5830HX202404");
  await page.getByLabel("品牌").fill("Honda");
  await page.getByLabel("型號").fill("Civic");
  await page.getByRole("button", { name: "儲存車檔" }).click();
  await expect(page).toHaveURL(/\/vehicles\/00000000-0000-4000-8000-000000000001$/);
  await page.getByLabel("選擇行車日誌").setInputFiles({ name: "trip.tsv", mimeType: "text/tab-separated-values", buffer: Buffer.from("time\trpm\n0\t1000\n") });
  await page.getByRole("button", { name: "上傳日誌" }).click();
  await expect(page.getByText("日誌已保存。現在可從列表選擇並診斷。")).toBeVisible();
  const slowUpload = async (route: import("@playwright/test").Route) => {
    if (route.request().method() !== "POST") return route.continue();
    await new Promise((resolve) => setTimeout(resolve, 2000));
    try { await route.fulfill({ status: 503, contentType: "application/json", body: '{"error":{"code":"V3_UNAVAILABLE"}}' }); }
    catch { /* The browser cancelled this request. */ }
  };
  await page.route("**/api/portal/vehicles/*/logs", slowUpload);
  await page.getByLabel("選擇行車日誌").setInputFiles({ name: "retry.tsv", mimeType: "text/tab-separated-values", buffer: Buffer.from("time\trpm\n0\t900\n") });
  await page.getByRole("button", { name: "上傳日誌" }).click();
  await page.getByRole("button", { name: "取消上傳" }).click();
  await expect(page.getByText("上傳已取消。")).toBeVisible();
  await page.unroute("**/api/portal/vehicles/*/logs", slowUpload);
  await page.getByRole("button", { name: "上傳日誌" }).click();
  await expect(page.getByText("日誌已保存。現在可從列表選擇並診斷。")).toBeVisible();
  await page.getByLabel("設備名稱").fill("新設備");
  await page.getByRole("button", { name: "發放憑證" }).click();
  await expect(page.getByText("FAKE-ONE-TIME-TOKEN")).toBeVisible();
  await page.getByRole("button", { name: "已保存，隱藏憑證" }).click();
  await expect(page.getByText("FAKE-ONE-TIME-TOKEN")).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("FAKE-ONE-TIME-TOKEN")).toHaveCount(0);
  expect(requests.filter((url) => new URL(url).origin !== new URL(page.url()).origin || new URL(url).pathname.startsWith("/v3/"))).toEqual([]);
});

test("an archived process still opens its partial report", async ({ page }) => {
  await login(page);
  await page.goto("/vehicles/00000000-0000-4000-8000-000000000001/conversations/88888888-8888-4888-8888-888888888888");
  await expect(page.getByText("診斷過程已歸檔；報告仍可查看。")).toBeVisible();
  await expect(page.getByText("部分報告", { exact: true })).toBeVisible();
  await expect(page.getByText("診斷提早結束")).toBeVisible();
  await expect(page.getByText("已查閱維修手冊，請檢查電裝系統。")).toBeVisible();
});

test.describe("stale conversation recovery", () => {
  test.use({ serviceWorkers: "block" });
  test("replayed done event refreshes a stale running conversation so its report appears", async ({ page }) => {
  await login(page);
  let detailReads = 0;
  await page.route(/\/api\/portal\/conversations\/66666666-6666-4666-8666-666666666666$/, async (route) => {
    detailReads += 1;
    if (detailReads !== 1) { await route.continue(); return; }
    const upstream = await route.fetch();
    const body = await upstream.json() as { conversation: Record<string, unknown> };
    body.conversation.status = "running";
    body.conversation.has_report = false;
    await route.fulfill({ response: upstream, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/vehicles/00000000-0000-4000-8000-000000000001/conversations/66666666-6666-4666-8666-666666666666");
  await expect(page.getByText("已查閱維修手冊，請檢查電裝系統。")).toBeVisible();
  expect(detailReads).toBeGreaterThanOrEqual(2);
  });
});
