import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { requirePortalSession } from "@/server/auth-session";
import { resetRateLimitsForTests } from "@/server/rate-limit";
import { GET as listVehicles } from "@/app/api/portal/workshops/[workshopId]/vehicles/route";
import { GET as getVehicle } from "@/app/api/portal/vehicles/[vehicleId]/route";

vi.mock("@/server/auth-session", () => ({ requirePortalSession: vi.fn() }));

const workshopId = "11111111-1111-4111-8111-111111111111";
const vehicleId = "00000000-0000-4000-8000-000000000001";
const car = { id: vehicleId, workshop_id: workshopId, vin: "JHMGK5830HX202404", plate: "STF-001", manufacturer: "Honda", model: "Civic", nickname: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", secret_internal: "must-not-leak" };
const session = { token: "server-only-token", user: { id: "user", username: "manager", email: null, display_name: null, memberships: [{ workshop_id: workshopId, workshop_name: "測試工作坊", role: "manager" }] } };
const request = (path: string) => new Request(`http://127.0.0.1:3001${path}`, { headers: { "x-real-ip": "198.51.100.51" } });

beforeEach(() => {
  process.env.STF_V3_API_BASE_URL = "http://127.0.0.1:4010";
  vi.mocked(requirePortalSession).mockResolvedValue(session);
  resetRateLimitsForTests();
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe("V3 vehicle BFF", () => {
  it("forwards a bounded search with the server token and strips upstream fields", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json([car])); vi.stubGlobal("fetch", fetch);
    const response = await listVehicles(request(`/api/portal/workshops/${workshopId}/vehicles?q=STF-001&offset=0`), { params: Promise.resolve({ workshopId }) });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ vehicles: [{ id: vehicleId, workshop_id: workshopId, vin: car.vin, plate: car.plate, manufacturer: car.manufacturer, model: car.model, nickname: car.nickname, created_at: car.created_at, updated_at: car.updated_at }], nextOffset: null });
    expect(fetch.mock.calls[0][0]).toContain(`/v3/workshops/${workshopId}/vehicles?limit=51&offset=0&q=STF-001`);
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe("Bearer server-only-token");
  });

  it("does not offer a next page when exactly 50 records exist", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(Array.from({ length: 50 }, () => car))));
    const response = await listVehicles(request(`/api/portal/workshops/${workshopId}/vehicles`), { params: Promise.resolve({ workshopId }) });
    const body = await response.json();
    expect(body.vehicles).toHaveLength(50);
    expect(body.nextOffset).toBeNull();
  });

  it("uses the 51st record only to detect the next page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(Array.from({ length: 51 }, () => car))));
    const response = await listVehicles(request(`/api/portal/workshops/${workshopId}/vehicles`), { params: Promise.resolve({ workshopId }) });
    const body = await response.json();
    expect(body.vehicles).toHaveLength(50);
    expect(body.nextOffset).toBe(50);
  });

  it("refuses a workshop outside the current user's memberships before fetching", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const other = "99999999-9999-4999-8999-999999999999";
    const response = await listVehicles(request(`/api/portal/workshops/${other}/vehicles`), { params: Promise.resolve({ workshopId: other }) });
    expect(response.status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects unbounded queries and maps upstream access failures", async () => {
    const invalid = await listVehicles(request(`/api/portal/workshops/${workshopId}/vehicles?q=${"x".repeat(51)}`), { params: Promise.resolve({ workshopId }) });
    expect(invalid.status).toBe(400);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 403 })));
    const denied = await listVehicles(request(`/api/portal/workshops/${workshopId}/vehicles`), { params: Promise.resolve({ workshopId }) });
    expect(denied.status).toBe(403);
  });

  it("reads a vehicle by UUID and excludes upstream private fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(car)));
    const response = await getVehicle(request(`/api/portal/vehicles/${vehicleId}`), { params: Promise.resolve({ vehicleId }) });
    expect(response.status).toBe(200);
    expect(JSON.stringify(await response.json())).not.toContain("secret_internal");
  });

  it("keeps 404 and 503 distinct for a vehicle", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
    const missing = await getVehicle(request(`/api/portal/vehicles/${vehicleId}`), { params: Promise.resolve({ vehicleId }) });
    expect(missing.status).toBe(404);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const unavailable = await getVehicle(request(`/api/portal/vehicles/${vehicleId}`), { params: Promise.resolve({ vehicleId }) });
    expect(unavailable.status).toBe(503);
  });
});
