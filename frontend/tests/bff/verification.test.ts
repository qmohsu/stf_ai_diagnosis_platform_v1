import { afterEach, describe, expect, it, vi } from "vitest";
import { DELETE, POST } from "@/app/api/portal/verification/route";

const profile = { plate: "AB1234", vin: "JHMGK5830HX202404", brand: "HONDA", model: "ACCORD", vehicle_class: "Saloon", fuel_type: "Petrol", powertrain_type: "ICE" };

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("verification BFF", () => {
  it("sets an HttpOnly browser-session cookie only after a four-field match", async () => {
    vi.stubEnv("PORTAL_PUBLIC_ORIGIN", "https://portal.test");
    vi.stubEnv("STF_V3_API_BASE_URL", "https://v3.test");
    stubProfiles([profile]);
    const response = await POST(request("POST", { plate: "ab-1234", vin: profile.vin, manufacturer: "honda", model: profile.model }));
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ canonicalPath: `/vehicles/vin/${profile.vin}` });
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain("portal_vehicle_context=");
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=strict");
    expect(cookie).not.toContain("Max-Age");
  });

  it("rejects mixed identities without setting a context cookie", async () => {
    vi.stubEnv("PORTAL_PUBLIC_ORIGIN", "https://portal.test");
    vi.stubEnv("STF_V3_API_BASE_URL", "https://v3.test");
    stubProfiles([profile]);
    const response = await POST(request("POST", { plate: profile.plate, vin: profile.vin, manufacturer: profile.brand, model: "OTHER" }));
    expect(response.status).toBe(422);
    expect(response.headers.get("set-cookie")).toBeNull();
    await expect(response.json()).resolves.toMatchObject({ error: { code: "VEHICLE_VERIFICATION_FAILED" } });
  });

  it("rejects a cross-origin mutation and clears the cookie through DELETE", async () => {
    vi.stubEnv("PORTAL_PUBLIC_ORIGIN", "https://portal.test");
    vi.stubEnv("STF_V3_API_BASE_URL", "https://v3.test");
    stubProfiles([profile]);
    const rejected = await POST(new Request("https://portal.test/api/portal/verification", { method: "POST", headers: { origin: "https://attacker.test", "content-type": "application/json" }, body: JSON.stringify({ plate: "AB1234", vin: profile.vin, manufacturer: "HONDA", model: "ACCORD" }) }));
    expect(rejected.status).toBe(403);
    const cleared = await DELETE(new Request("https://portal.test/api/portal/verification", { method: "DELETE", headers: { origin: "https://portal.test", cookie: "portal_access_token=test-token" } }));
    expect(cleared.status).toBe(200);
    expect(cleared.headers.get("set-cookie")).toContain("Max-Age=0");
  });
});

function request(method: "POST", body: unknown): Request {
  return new Request("https://portal.test/api/portal/verification", { method, headers: { origin: "https://portal.test", "content-type": "application/json", cookie: "portal_access_token=test-token" }, body: JSON.stringify(body) });
}

function stubProfiles(value: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const body = url.endsWith("/v3/users/me")
      ? { id: "11111111-1111-1111-1111-111111111111", username: "operator", email: null, display_name: null, memberships: [] }
      : value;
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }));
}
