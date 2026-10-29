import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST as login } from "@/app/api/auth/login/route";
import { POST as logout } from "@/app/api/auth/logout/route";
import { GET as vehicles } from "@/app/api/portal/vehicles/route";
import { resetRateLimitsForTests } from "@/server/rate-limit";

const user = { id: "11111111-1111-1111-1111-111111111111", username: "operator", email: null, display_name: "Operator", memberships: [] };

beforeEach(() => {
  resetRateLimitsForTests();
  vi.stubEnv("PORTAL_PUBLIC_ORIGIN", "https://portal.test");
  vi.stubEnv("STF_V3_API_BASE_URL", "https://v3.test");
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("authentication BFF", () => {
  it("forwards an OAuth password form, verifies the user, and keeps the JWT HttpOnly", async () => {
    const token = jwtWithExpiration(Math.floor(Date.now() / 1000) + 12 * 60 * 60);
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, init });
      if (url.endsWith("/v3/auth/login")) return json({ access_token: token, token_type: "bearer" });
      if (url.endsWith("/v3/users/me")) return json(user);
      return json({ detail: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await login(loginRequest({ username: "operator", password: "correct horse" }));
    expect(response.status).toBe(200);
    const responseBody = await response.json();
    expect(responseBody).toEqual({ user });
    expect(calls[0].url).toBe("https://v3.test/v3/auth/login");
    expect(calls[0].init?.headers).toMatchObject({ "Content-Type": "application/x-www-form-urlencoded" });
    expect(calls[0].init?.body?.toString()).toBe("username=operator&password=correct+horse");
    expect(calls[1].url).toBe("https://v3.test/v3/users/me");
    expect(calls[1].init?.headers).toMatchObject({ Authorization: `Bearer ${token}` });
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`portal_access_token=${token}`);
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=strict");
    expect(cookie).toMatch(/Max-Age=43[01]\d\d/);
    expect(cookie).toContain("portal_vehicle_context=;");
    expect(JSON.stringify(responseBody)).not.toContain(token);
  });

  it("maps bad credentials to one generic error without setting a session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: "LOGIN_BAD_CREDENTIALS" }, 400)));
    const response = await login(loginRequest({ username: "missing", password: "wrong" }));
    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toMatchObject({ error: { code: "AUTH_INVALID_CREDENTIALS" } });
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("rejects malformed and cross-origin login requests before contacting V3", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const malformed = await login(loginRequest({ username: "", password: "" }));
    expect(malformed.status).toBe(400);
    const crossOrigin = await login(new Request("https://portal.test/api/auth/login", { method: "POST", headers: { origin: "https://evil.test", "content-type": "application/json" }, body: JSON.stringify({ username: "operator", password: "secret" }) }));
    expect(crossOrigin.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports an unavailable V3 service without exposing upstream details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")));
    const response = await login(loginRequest({ username: "operator", password: "secret" }));
    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({ error: { code: "AUTH_SERVICE_UNAVAILABLE" } });
  });

  it("always clears the account and vehicle cookies on logout", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({})); vi.stubGlobal("fetch", fetchMock);
    const response = await logout(new Request("https://portal.test/api/auth/logout", { method: "POST", headers: { origin: "https://portal.test", cookie: "portal_access_token=test-token; portal_vehicle_context=old" } }));
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ loggedOut: true });
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain("portal_access_token=;");
    expect(cookie).toContain("portal_vehicle_context=;");
    expect(fetchMock).toHaveBeenCalledWith("https://v3.test/v3/auth/logout", expect.objectContaining({ method: "POST" }));
  });

  it("blocks direct portal API access without a V3 session", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const response = await vehicles(new Request("https://portal.test/api/portal/vehicles"));
    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toMatchObject({ error: { code: "AUTH_REQUIRED" } });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

function loginRequest(body: unknown): Request {
  return new Request("https://portal.test/api/auth/login", { method: "POST", headers: { origin: "https://portal.test", "content-type": "application/json", "x-forwarded-for": "203.0.113.20" }, body: JSON.stringify(body) });
}

function jwtWithExpiration(exp: number): string {
  const encode = (value: unknown) => btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ exp })}.signature`;
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}
