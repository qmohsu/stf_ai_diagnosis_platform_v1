import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { requirePortalSession } from "@/server/auth-session";
import { resetRateLimitsForTests } from "@/server/rate-limit";
import { POST as register } from "@/app/api/auth/register/route";
import { POST as createVehicle } from "@/app/api/portal/workshops/[workshopId]/vehicles/route";
import { POST as uploadLog } from "@/app/api/portal/vehicles/[vehicleId]/logs/route";
import { GET as listDevices, POST as createDevice } from "@/app/api/portal/vehicles/[vehicleId]/devices/route";
import { DELETE as revokeDevice } from "@/app/api/portal/devices/[deviceId]/route";

vi.mock("@/server/auth-session", () => ({ requirePortalSession: vi.fn() }));
const origin = "https://stf-diagnosis.dev";
const workshopId = "11111111-1111-4111-8111-111111111111";
const vehicleId = "00000000-0000-4000-8000-000000000001";
const deviceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const car = { id: vehicleId, workshop_id: workshopId, vin: "JHMGK5830HX202404", plate: null, manufacturer: "Honda", model: "Civic", nickname: null, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", secret: "hidden" };
const device = { id: deviceId, vehicle_id: vehicleId, label: "Jetson A", created_at: "2026-10-01T00:00:00Z", last_seen_at: null, revoked_at: null, secret: "hidden" };
const session = { token: "server-token", user: { id: "user", username: "manager", email: null, display_name: null, memberships: [{ workshop_id: workshopId, workshop_name: "Test", role: "manager" }] } };
const req = (path: string, method: string, body?: BodyInit, headers: Record<string, string> = {}) => new Request(origin + path, { method, headers: { origin, "x-real-ip": "198.51.100.81", ...headers }, body });

beforeEach(() => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("PORTAL_PUBLIC_ORIGIN", origin);
  vi.stubEnv("STF_V3_API_BASE_URL", "http://v3.internal:8002");
  vi.mocked(requirePortalSession).mockResolvedValue(session);
  resetRateLimitsForTests();
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.resetAllMocks(); });

describe("V3 write BFF", () => {
  it("registers with an invite through V3 without setting a session or exposing upstream fields", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ ...session.user, secret: "hidden" }, { status: 201 }));
    vi.stubGlobal("fetch", fetch);
    const response = await register(req("/api/auth/register", "POST", JSON.stringify({ username: "new.user", password: "strong-password", invite_code: "TEST-INVITE", secret_internal: "do-not-forward" }), { "content-type": "application/json" }));
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ registered: true });
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(response.cookies.get("portal_access_token")).toBeUndefined();
    expect(fetch.mock.calls[0][0]).toBe("http://v3.internal:8002/v3/auth/register");
    expect(fetch.mock.calls[0][1].body).not.toContain("secret_internal");
  });

  it.each([
    [422, "invite_code_invalid", "INVITE_INVALID"],
    [409, "username_exists", "USERNAME_EXISTS"],
    [409, "email_exists", "EMAIL_EXISTS"],
  ])("maps registration %s %s", async (status, code, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ code }, { status })));
    const response = await register(req("/api/auth/register", "POST", JSON.stringify({ username: "new.user", password: "strong-password", invite_code: "TEST" }), { "content-type": "application/json" }));
    expect((await response.json()).error.code).toBe(expected);
  });

  it("blocks foreign origins and nonmember vehicle creation before calling V3", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const foreign = await createVehicle(req(`/api/portal/workshops/${workshopId}/vehicles`, "POST", "{}", { origin: "https://other.example" }), { params: Promise.resolve({ workshopId }) });
    expect(foreign.status).toBe(403);
    const other = "99999999-9999-4999-8999-999999999999";
    const nonmember = await createVehicle(req(`/api/portal/workshops/${other}/vehicles`, "POST", "{}"), { params: Promise.resolve({ workshopId: other }) });
    expect(nonmember.status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("creates a vehicle for a technician and strips private fields", async () => {
    vi.mocked(requirePortalSession).mockResolvedValue({ ...session, user: { ...session.user, memberships: [{ ...session.user.memberships[0], role: "technician" }] } });
    const fetch = vi.fn().mockResolvedValue(Response.json(car, { status: 201 })); vi.stubGlobal("fetch", fetch);
    const response = await createVehicle(req(`/api/portal/workshops/${workshopId}/vehicles`, "POST", JSON.stringify({ vin: car.vin, manufacturer: "Honda", model: "Civic", secret_internal: "do-not-forward" }), { "content-type": "application/json" }), { params: Promise.resolve({ workshopId }) });
    expect(response.status).toBe(201);
    expect(JSON.stringify(await response.json())).not.toContain("secret");
    expect(fetch.mock.calls[0][1].headers.get("Authorization")).toBe("Bearer server-token");
    expect(fetch.mock.calls[0][1].body).not.toContain("secret_internal");
  });

  it("maps duplicate VIN and upload format, VIN, size, and outage errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ code: "vin_exists" }, { status: 409 })));
    const vehicle = await createVehicle(req(`/api/portal/workshops/${workshopId}/vehicles`, "POST", JSON.stringify({ vin: car.vin, manufacturer: "Honda", model: "Civic" }), { "content-type": "application/json" }), { params: Promise.resolve({ workshopId }) });
    expect((await vehicle.json()).error.code).toBe("VIN_EXISTS");
    for (const [status, code, mapped] of [[422, "unsupported_format", "UNSUPPORTED_FORMAT"], [422, "vin_mismatch", "VIN_MISMATCH"], [413, "file_too_large", "FILE_TOO_LARGE"]] as const) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ code }, { status })));
      const body = new FormData(); body.set("file", new File(["test"], "trip.tsv"));
      const response = await uploadLog(req(`/api/portal/vehicles/${vehicleId}/logs`, "POST", body), { params: Promise.resolve({ vehicleId }) });
      expect((await response.json()).error.code).toBe(mapped);
    }
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const body = new FormData(); body.set("file", new File(["test"], "trip.tsv"));
    expect((await uploadLog(req(`/api/portal/vehicles/${vehicleId}/logs`, "POST", body), { params: Promise.resolve({ vehicleId }) })).status).toBe(503);
  });

  it("forwards multipart upload without an eight-second timeout and whitelists the log", async () => {
    const body = new FormData(); body.set("file", new File(["test"], "trip.tsv"));
    const upstream = { id: "44444444-4444-4444-8444-444444444444", vehicle_id: vehicleId, format: "tsv", size_bytes: 4, original_filename: "trip.tsv", source: "web", recorded_start: null, recorded_end: null, uploaded_at: "2026-10-01T00:00:00Z", duplicate: false, secret: "hidden" };
    const fetch = vi.fn().mockResolvedValue(Response.json(upstream, { status: 201 })); vi.stubGlobal("fetch", fetch);
    const response = await uploadLog(req(`/api/portal/vehicles/${vehicleId}/logs`, "POST", body), { params: Promise.resolve({ vehicleId }) });
    expect(response.status).toBe(201);
    expect(JSON.stringify(await response.json())).not.toContain("secret");
    expect(fetch.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
    expect(fetch.mock.calls[0][1].headers.get("Content-Type")).toContain("multipart/form-data");
  });

  it("enforces manager access and exposes a token only on creation", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json(car)).mockResolvedValueOnce(Response.json([device]));
    vi.stubGlobal("fetch", fetch);
    const listed = await listDevices(req(`/api/portal/vehicles/${vehicleId}/devices`, "GET"), { params: Promise.resolve({ vehicleId }) });
    expect(JSON.stringify(await listed.json())).not.toContain("secret");
    expect(fetch).toHaveBeenCalledTimes(2);
    vi.mocked(requirePortalSession).mockResolvedValue({ ...session, user: { ...session.user, memberships: [{ ...session.user.memberships[0], role: "technician" }] } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(car)));
    const denied = await createDevice(req(`/api/portal/vehicles/${vehicleId}/devices`, "POST", JSON.stringify({ label: "Jetson" }), { "content-type": "application/json" }), { params: Promise.resolve({ vehicleId }) });
    expect(denied.status).toBe(403);
    vi.mocked(requirePortalSession).mockResolvedValue(session);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json(car)).mockResolvedValueOnce(Response.json({ ...device, token: "FAKE-ONCE" }, { status: 201 })));
    const created = await createDevice(req(`/api/portal/vehicles/${vehicleId}/devices`, "POST", JSON.stringify({ label: "Jetson" }), { "content-type": "application/json" }), { params: Promise.resolve({ vehicleId }) });
    expect((await created.json()).token).toBe("FAKE-ONCE");
  });

  it("requires same origin for credential revocation", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const response = await revokeDevice(req(`/api/portal/devices/${deviceId}`, "DELETE", undefined, { origin: "https://other.example" }), { params: Promise.resolve({ deviceId }) });
    expect(response.status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
});
