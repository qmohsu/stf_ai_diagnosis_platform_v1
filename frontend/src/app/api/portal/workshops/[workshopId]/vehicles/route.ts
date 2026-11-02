import { requirePortalSession } from "@/server/auth-session";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { UUID_RE, v3Get, vehicleListFromV3 } from "@/server/v3-client";
import { v3PortalJson } from "@/server/diagnosis-client";
import { requireSameOrigin } from "@/server/request-security";
import { vehicleFromV3 } from "@/server/v3-client";

export async function GET(request: Request, context: { params: Promise<{ workshopId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const { workshopId } = await context.params;
    if (!UUID_RE.test(workshopId)) throw new PortalError(404, "NOT_FOUND", "找不到工作坊");
    if (!session.user.memberships.some((membership) => membership.workshop_id === workshopId)) {
      throw new PortalError(404, "NOT_FOUND", "找不到工作坊");
    }
    const search = new URL(request.url).searchParams;
    const q = (search.get("q") ?? "").trim();
    const rawOffset = search.get("offset") ?? "0";
    if (q.length > 50 || !/^(0|[1-9][0-9]*)$/.test(rawOffset) || Number(rawOffset) > 1_000_000) {
      throw new PortalError(400, "INVALID_QUERY", "搜尋條件不正確");
    }
    const query = new URLSearchParams({ limit: "51", offset: rawOffset });
    if (q) query.set("q", q);
    const records = vehicleListFromV3(await v3Get(`/v3/workshops/${workshopId}/vehicles?${query}`, session.token));
    return portalJson({ vehicles: records.slice(0, 50), nextOffset: records.length > 50 ? Number(rawOffset) + 50 : null });
  } catch (error) { return portalError(error); }
}

export async function POST(request: Request, context: { params: Promise<{ workshopId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    const session = await requirePortalSession(request);
    const { workshopId } = await context.params;
    if (!UUID_RE.test(workshopId) || !session.user.memberships.some((item) => item.workshop_id === workshopId)) {
      throw new PortalError(404, "NOT_FOUND", "找不到工作坊");
    }
    const body: unknown = await request.json().catch(() => null);
    if (!validVehicle(body)) throw new PortalError(400, "INVALID_VEHICLE", "請檢查 VIN、品牌與型號");
    const value = await v3PortalJson(`/v3/workshops/${workshopId}/vehicles`, session.token, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        vin: body.vin.trim().toUpperCase(), manufacturer: body.manufacturer.trim(), model: body.model.trim(),
        plate: body.plate ?? null, nickname: body.nickname ?? null,
      }),
    });
    return portalJson({ vehicle: vehicleFromV3(value) }, { status: 201 });
  } catch (error) { return portalError(error); }
}

function validVehicle(value: unknown): value is {
  vin: string; manufacturer: string; model: string; plate?: string | null; nickname?: string | null;
} {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.vin === "string" && /^[A-HJ-NPR-Z0-9]{17}$/.test(item.vin.trim().toUpperCase())
    && typeof item.manufacturer === "string" && item.manufacturer.trim().length > 0 && item.manufacturer.length <= 100
    && typeof item.model === "string" && item.model.trim().length > 0 && item.model.length <= 100
    && (item.plate === undefined || item.plate === null || (typeof item.plate === "string" && item.plate.length <= 20))
    && (item.nickname === undefined || item.nickname === null || (typeof item.nickname === "string" && item.nickname.length <= 100));
}
