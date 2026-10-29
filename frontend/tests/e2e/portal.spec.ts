import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("login is responsive, accessible, and gates vehicle verification", async ({ page }, testInfo) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: /員工登入|员工登录/ })).toBeVisible();
  await expect(page.getByLabel(/用戶名稱|用户名/)).toHaveAttribute("autocomplete", "username");
  await expect(page.locator("#login-password")).toHaveAttribute("autocomplete", "current-password");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page).toHaveScreenshot(`login-${testInfo.project.name}.png`, { fullPage: true, animations: "disabled" });
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);

  await page.getByLabel(/用戶名稱|用户名/).fill("operator");
  const password = page.locator("#login-password");
  await password.fill("wrong");
  await page.getByRole("button", { name: /顯示密碼|显示密码/ }).click();
  await expect(password).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: /登入|登录/, exact: true }).click();
  await expect(page.locator(".login-error")).toContainText(/用戶名稱或密碼不正確|用户名或密码不正确/);
  await expect(password).toHaveValue("");

  await password.fill("correct horse battery staple");
  await page.getByRole("button", { name: /登入|登录/, exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: /車輛身份驗證|车辆身份验证/ })).toBeVisible();
});

test("verification is a single progressive form and enters the selected vehicle directly", async ({ page }, testInfo) => {
  await signIn(page);
  await expect(page.getByRole("heading", { name: /車輛身份驗證|车辆身份验证/, level: 1 })).toBeVisible();
  await expect(page.locator(".verification-card")).toHaveCount(1);
  await expect(page.getByLabel("VIN")).toBeDisabled();
  await expect(page.getByLabel(/品牌/)).toBeDisabled();
  await expect(page.getByLabel(/型號|型号/)).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page).toHaveScreenshot(`verification-initial-${testInfo.project.name}.png`, { fullPage: true, animations: "disabled" });

  await choosePlate(page, "AB1234");
  await expect(page.getByLabel("VIN")).toHaveValue("JHMGK5830HX202404");
  await expect(page.getByLabel(/品牌/)).toHaveValue("HONDA");
  await expect(page.getByLabel(/型號|型号/)).toHaveValue("ACCORD");
  await expect(page.getByRole("button", { name: /驗證並進入車輛主頁|验证并进入车辆主页/ })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page).toHaveScreenshot(`verification-filled-${testInfo.project.name}.png`, { fullPage: true, animations: "disabled" });
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  await page.getByRole("button", { name: /驗證並進入車輛主頁|验证并进入车辆主页/ }).click();
  await expect(page).toHaveURL(/\/vehicles\/vin\/JHMGK5830HX202404$/);
  await expect(page.getByTestId("vehicle-current-fault-kpi")).toBeVisible();
  expect(await page.evaluate(() => document.cookie)).not.toMatch(/portal_vehicle_context/);
});

test("missing VIN stays visible as a dash and cannot be verified", async ({ page }) => {
  await signIn(page);
  await choosePlate(page, "UY8280");
  await expect(page.getByLabel("VIN")).toHaveValue("-");
  await expect(page.getByLabel("VIN")).toBeDisabled();
  await expect(page.getByText(/資料來源尚未提供，暫時無法驗證|数据源尚未提供，暂时无法验证/)).toBeVisible();
  await expect(page.getByRole("button", { name: /驗證並進入車輛主頁|验证并进入车辆主页/ })).toBeDisabled();
});

test("verification comboboxes support keyboard confirmation and model dependency", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-1280", "one browser project is sufficient for keyboard semantics");
  await signIn(page);
  const plate = page.getByRole("combobox", { name: /車牌|车牌/ });
  await plate.fill("AB");
  await expect(page.getByRole("option", { name: /AB1234/ })).toBeVisible();
  await plate.press("Escape");
  await expect(page.getByRole("option", { name: /AB1234/ })).toBeHidden();
  await plate.press("ArrowDown");
  await plate.press("Enter");
  await expect(plate).toHaveValue("AB1234");
  const brand = page.getByRole("combobox", { name: /品牌/ });
  await brand.fill("hon");
  await expect(page.getByRole("option", { name: "HONDA" })).toBeVisible();
  await brand.press("ArrowDown");
  await brand.press("Enter");
  await expect(brand).toHaveValue("HONDA");
  await expect(page.getByLabel(/型號|型号/)).toBeEnabled();
  await page.getByLabel(/型號|型号/).selectOption("ACCORD");
});

test("unverified and cross-vehicle URLs return to verification while a valid session survives refresh", async ({ page, context }) => {
  await signIn(page);
  await page.goto("/vehicles/vin/JHMGK5830HX202404");
  await expect(page).toHaveURL(/\/$/);
  await verifyVehicle(page);
  await page.reload();
  await expect(page.getByTestId("vehicle-current-fault-kpi")).toBeVisible();
  const secondPage = await context.newPage();
  await secondPage.goto("/");
  await expect(secondPage).toHaveURL(/\/vehicles\/vin\/JHMGK5830HX202404$/);
  await secondPage.close();
  await page.goto("/vehicles/plate/UY8280");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: /車輛身份驗證|车辆身份验证/ })).toBeVisible();
});

test("verified vehicle opens the dashboard immediately and keeps history supplemental", async ({ page }, testInfo) => {
  await verifyVehicle(page);
  await expect(page.getByTestId("vehicle-current-fault-kpi")).toContainText("-");
  await expect(page.getByTestId("vehicle-risk-kpi")).toContainText("-");
  await expect(page.getByTestId("vehicle-latest-data-kpi")).toContainText("2026");
  await expect(page.getByTestId("vehicle-coverage-kpi")).toContainText("-");
  await expect(page.getByTestId("vehicle-current-faults")).toContainText(/目前故障|当前故障/);
  await expect(page.getByTestId("vehicle-risk-forecast")).toContainText(/高風險預測|高风险预测/);
  await expect(page.getByText("歷史檢查記錄")).toBeHidden();
  await expect(page).toHaveScreenshot(`vehicle-${testInfo.project.name}.png`, { fullPage: true, animations: "disabled" });
  if (testInfo.project.name.startsWith("mobile") || testInfo.project.name.startsWith("tablet")) await page.getByRole("button", { name: /整車系統|整车系统/ }).click();
  const systemNode = page.getByRole("treeitem", { name: /引擎系統/ });
  const componentNode = page.getByRole("treeitem", { name: /燃油濾清器/ });
  await expect(systemNode).toHaveCSS("--tree-indent", "0em");
  await expect(componentNode).toHaveCSS("--tree-indent", "2em");
  await componentNode.getByRole("button").press("ArrowRight");
  await expect(page.getByRole("treeitem", { name: /燃油噴射器/ })).toHaveCSS("--tree-indent", "4em");
  await componentNode.getByRole("button").click();
  await expect(page).toHaveURL(/\?scope=CMP-FILTER$/);
  await expect(page.getByRole("link", { name: /部件詳情|部件详情/ })).toBeVisible();
  await page.locator(".vehicle-more-data summary").click();
  await expect(page.getByText("歷史檢查記錄")).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("switch vehicle clears context and cached sanitized data", async ({ page }) => {
  await verifyVehicle(page);
  await page.evaluate(async () => { const cache = await caches.open("test-sanitized-data"); await cache.put("/cached", new Response("cached")); });
  await page.getByRole("button", { name: /切換車輛|切换车辆/ }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: /車輛身份驗證|车辆身份验证/ })).toBeVisible();
  expect(await page.evaluate(async () => (await caches.keys()).some((name) => name.includes("sanitized-data")))).toBe(false);
});

test("logout clears account, vehicle context, and cached data", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-1280", "one browser project is sufficient for logout semantics");
  await verifyVehicle(page);
  await page.evaluate(async () => { const cache = await caches.open("test-sanitized-data"); await cache.put("/cached", new Response("cached")); });
  await page.getByRole("button", { name: /登出|退出/ }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: /員工登入|员工登录/ })).toBeVisible();
  expect(await page.evaluate(async () => (await caches.keys()).some((name) => name.includes("sanitized-data")))).toBe(false);
  const cookieNames = (await page.context().cookies()).map((cookie) => cookie.name);
  expect(cookieNames).not.toContain("portal_access_token");
  expect(cookieNames).not.toContain("portal_vehicle_context");
});

test("component detail exposes cleaned lifecycle data", async ({ page }) => {
  await verifyVehicle(page);
  await page.goto("/vehicles/vin/JHMGK5830HX202404/components/CMP-FILTER?returnScope=CMP-FILTER");
  await expect(page.getByRole("heading", { name: "燃油濾清器" })).toBeVisible();
  await expect(page.getByTestId("component-status").getByText("removed", { exact: true })).toBeVisible();
  await expect(page.getByTestId("current-fault")).toContainText(/歷史故障記錄|历史故障记录/);
  await expect(page.getByTestId("manual-match").getByRole("button")).toBeDisabled();
  await expect(page.getByTestId("risk-forecast").getByText("-", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("maintenance-history")).toContainText("WO-1001");
  await expect(page.getByTestId("data-status")).toContainText("1");
  await expect(page.getByText("WO-1001", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("hidden.xlsx")).toHaveCount(0);
  await page.locator(".component-more summary").click();
  await expect(page.getByText(/installed → removed/)).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("every BOM node uses the same detail frame and missing values stay explicit", async ({ page }) => {
  await verifyVehicle(page);
  await page.goto("/vehicles/vin/JHMGK5830HX202404/components/CMP-INJECTOR");
  await expect(page.getByRole("heading", { name: "燃油噴射器" })).toBeVisible();
  await expect(page.getByTestId("component-status").getByText("-", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("current-fault")).toContainText("-");
  await expect(page.getByTestId("risk-forecast")).toContainText("-");
  await expect(page.getByTestId("maintenance-history")).toContainText("-");
  await expect(page.getByTestId("data-status")).toBeVisible();
});

for (const oldPath of ["/register", "/reset-password"]) {
  test(`${oldPath} no longer exists`, async ({ request }) => { expect((await request.get(oldPath)).status()).toBe(404); });
}

test("legacy verification path redirects to the root form", async ({ page }) => { await signIn(page); await page.goto("/verify-vehicle"); await expect(page).toHaveURL(/\/$/); await expect(page.getByRole("heading", { name: /車輛身份驗證|车辆身份验证/ })).toBeVisible(); });

test("security headers are present on the public login", async ({ request }) => { const response = await request.get("/login"); expect(response.headers()["x-frame-options"]).toBe("DENY"); expect(response.headers()["x-content-type-options"]).toBe("nosniff"); expect(response.headers()["x-robots-tag"]).toBe("noindex, nofollow"); expect(response.headers()["content-security-policy"]).toContain("worker-src 'self'"); });

async function choosePlate(page: Page, plate: string) {
  const input = page.getByRole("combobox", { name: /車牌|车牌/ });
  const responsePromise = page.waitForResponse((response) => response.url().includes("/api/portal/verification/plates?"));
  await input.fill(plate);
  const response = await responsePromise;
  expect(response.status(), await response.text()).toBe(200);
  await page.getByRole("option", { name: new RegExp(plate) }).click();
  await expect(input).toHaveValue(plate);
}

async function verifyVehicle(page: Page) {
  await signIn(page);
  await choosePlate(page, "AB1234");
  await page.getByRole("button", { name: /驗證並進入車輛主頁|验证并进入车辆主页/ }).click();
  await expect(page).toHaveURL(/\/vehicles\/vin\/JHMGK5830HX202404$/);
  await page.waitForLoadState("networkidle");
}

async function signIn(page: Page) {
  await page.goto("/login");
  if (!/\/login(?:\?|$)/.test(page.url())) return;
  await page.getByLabel(/用戶名稱|用户名/).fill("operator");
  await page.locator("#login-password").fill("correct horse battery staple");
  await page.getByRole("button", { name: /登入|登录/, exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.waitForLoadState("networkidle");
}
