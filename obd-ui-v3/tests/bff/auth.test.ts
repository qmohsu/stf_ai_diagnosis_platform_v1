import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST as login } from "@/app/api/auth/login/route";
import { POST as logout } from "@/app/api/auth/logout/route";
import { requirePortalSession } from "@/server/auth-session";
import { resetRateLimitsForTests } from "@/server/rate-limit";

const origin = "https://stf-diagnosis.dev";
const user = { id: "user-id", username: "manager", email: null, display_name: "技師", memberships: [{ workshop_id: "11111111-1111-4111-8111-111111111111", workshop_name: "測試工作坊", role: "manager" }] };
const token = () => `eyJhbGciOiJIUzI1NiJ9.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }))}.signature`;
const loginRequest = (headers: Record<string, string> = {}) => new Request(`${origin}/api/auth/login`, {
  method: "POST", headers: { origin, "content-type": "application/json", "x-real-ip": "198.51.100.71", ...headers },
  body: JSON.stringify({ username: "manager", password: "valid-password" }),
});
const logoutRequest = (cookie: string, headers: Record<string, string> = {}) => new Request(`${origin}/api/auth/logout`, {
  method: "POST", headers: { origin, cookie, "x-real-ip": "198.51.100.71", ...headers },
});

beforeEach(() => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("PORTAL_PUBLIC_ORIGIN", origin);
  vi.stubEnv("STF_V3_API_BASE_URL", "http://v3.internal:8002");
  resetRateLimitsForTests();
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.clearAllMocks(); });

describe("authentication BFF", () => {
  it("uses the V3 password form and /users/me, returning the token only in a protected cookie", async () => {
    const accessToken = token();
    const fetch = vi.fn().mockResolvedValueOnce(Response.json({ access_token: accessToken, token_type: "bearer" })).mockResolvedValueOnce(Response.json(user));
    vi.stubGlobal("fetch", fetch);
    const response = await login(loginRequest());

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ user });
    const cookie = response.cookies.get("portal_access_token");
    expect(cookie).toMatchObject({ value: accessToken, httpOnly: true, secure: true, sameSite: "strict", path: "/" });
    expect(fetch.mock.calls[0][0]).toBe("http://v3.internal:8002/v3/auth/login");
    expect(fetch.mock.calls[0][1].headers["Content-Type"]).toBe("application/x-www-form-urlencoded");
    expect(fetch.mock.calls[0][1].body.toString()).toBe("username=manager&password=valid-password");
    expect(fetch.mock.calls[1][0]).toBe("http://v3.internal:8002/v3/users/me");
    expect(fetch.mock.calls[1][1].headers.Authorization).toBe(`Bearer ${accessToken}`);
  });

  it("issue #270 accepts omitted optional UserOut fields and strips unapproved user fields", async () => {
    const accessToken = token();
    const upstreamUser = {
      id: user.id,
      username: user.username,
      memberships: [{ ...user.memberships[0], secret_internal: "membership-secret" }],
      secret_internal: "user-secret",
    };
    const fetch = vi.fn().mockResolvedValueOnce(Response.json({ access_token: accessToken, token_type: "bearer" })).mockResolvedValueOnce(Response.json(upstreamUser));
    vi.stubGlobal("fetch", fetch);

    const response = await login(loginRequest());
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toEqual({ user: {
      id: user.id,
      username: user.username,
      email: null,
      display_name: null,
      memberships: user.memberships,
    } });
    expect(JSON.stringify(body)).not.toContain("secret_internal");
    expect(response.cookies.get("portal_access_token")?.value).toBe(accessToken);
  });

  it.each([
    ["email", { ...user, email: 42 }],
    ["display_name", { ...user, display_name: false }],
    ["membership role", { ...user, memberships: [{ ...user.memberships[0], role: 42 }] }],
  ])("issue #270 rejects an invalid %s type in UserOut", async (_field, invalidUser) => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json({ access_token: token(), token_type: "bearer" })).mockResolvedValueOnce(Response.json(invalidUser));
    vi.stubGlobal("fetch", fetch);

    const response = await login(loginRequest());
    expect(response.status).toBe(503);
    expect((await response.json()).error.code).toBe("AUTH_SERVICE_UNAVAILABLE");
    expect(response.cookies.get("portal_access_token")).toBeUndefined();
  });

  it("rejects foreign or missing Origin and fails closed without production Origin configuration", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect((await login(loginRequest({ origin: "https://other.example" }))).status).toBe(403);
    expect((await logout(logoutRequest("", { origin: "" }))).status).toBe(403);
    vi.stubEnv("PORTAL_PUBLIC_ORIGIN", "");
    const unconfigured = await login(loginRequest());
    expect(unconfigured.status).toBe(503);
    expect((await unconfigured.json()).error.code).toBe("ORIGIN_CONFIG");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("keeps invalid credentials, invalid session and upstream failure distinct", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 400 })));
    const invalid = await login(loginRequest());
    expect(invalid.status).toBe(401);
    expect((await invalid.json()).error.code).toBe("AUTH_INVALID_CREDENTIALS");

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    const down = await login(loginRequest());
    expect(down.status).toBe(503);
    expect((await down.json()).error.code).toBe("AUTH_SERVICE_UNAVAILABLE");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    await expect(requirePortalSession(new Request(`${origin}/api/portal/vehicles/any`, { headers: { cookie: `portal_access_token=${token()}` } }))).rejects.toMatchObject({ status: 401, code: "AUTH_REQUIRED" });
  });

  it("clears the token and legacy context cookie on logout", async () => {
    const accessToken = token();
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 })); vi.stubGlobal("fetch", fetch);
    const response = await logout(logoutRequest(`portal_access_token=${accessToken}; portal_vehicle_context=old`));
    expect(response.status).toBe(200);
    expect(response.cookies.get("portal_access_token")).toMatchObject({ value: "", maxAge: 0, httpOnly: true, secure: true, sameSite: "strict" });
    expect(response.cookies.get("portal_vehicle_context")).toMatchObject({ value: "", maxAge: 0 });
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${accessToken}`);
  });
});
