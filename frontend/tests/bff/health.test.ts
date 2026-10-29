import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/health/route";
afterEach(() => vi.unstubAllGlobals());
describe("health endpoint", () => {
  it("checks the vehicle platform", async () => { vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("[]", { status: 200, headers: { "content-type": "application/json" } }))); const response = await GET(); expect(response.status).toBe(200); await expect(response.json()).resolves.toMatchObject({ status: "ok", vehiclePlatform: "ok" }); });
  it("returns 503 when the vehicle platform is unavailable", async () => { vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline"))); const response = await GET(); expect(response.status).toBe(503); await expect(response.json()).resolves.toMatchObject({ status: "degraded", vehiclePlatform: "unavailable" }); });
});
