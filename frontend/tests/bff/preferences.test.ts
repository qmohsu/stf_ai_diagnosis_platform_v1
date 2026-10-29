import { describe, expect, it } from "vitest";
import { POST } from "@/app/api/preferences/locale/route";
describe("locale preference", () => {
  it("rejects a mismatched origin", async () => { process.env.PORTAL_PUBLIC_ORIGIN = "https://portal.example"; const response = await POST(new Request("https://portal.example/api/preferences/locale", { method: "POST", headers: { origin: "https://evil.example", "content-type": "application/json" }, body: JSON.stringify({ locale: "zh-CN" }) })); expect(response.status).toBe(403); });
  it("sets only the non-sensitive locale cookie", async () => { process.env.PORTAL_PUBLIC_ORIGIN = "https://portal.example"; const response = await POST(new Request("https://portal.example/api/preferences/locale", { method: "POST", headers: { origin: "https://portal.example", "content-type": "application/json" }, body: JSON.stringify({ locale: "zh-CN" }) })); expect(response.status).toBe(200); expect(response.headers.get("set-cookie")).toContain("portal_locale=zh-CN"); expect(response.headers.get("set-cookie")).not.toMatch(/portal_auth|portal_vehicle|portal_idle/i); });
});
