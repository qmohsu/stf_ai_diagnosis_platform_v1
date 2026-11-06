import { PortalError } from "@/server/errors";
import type { VehicleRecord } from "@/lib/v3-domain";

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function v3Get(path: string, token: string): Promise<unknown> {
  const base = process.env.STF_V3_API_BASE_URL?.trim();
  if (!base) throw new PortalError(503, "V3_CONFIG", "V3 服務尚未設定");
  let response: Response;
  try {
    response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
      headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
  } catch {
    throw new PortalError(503, "V3_UNAVAILABLE", "V3 服務暫時無法使用");
  }
  if (response.status === 401) throw new PortalError(401, "AUTH_REQUIRED", "登入狀態已失效");
  if (response.status === 403) throw new PortalError(403, "ACCESS_DENIED", "無權查看此資料");
  if (response.status === 404) throw new PortalError(404, "NOT_FOUND", "找不到所需資料");
  if (!response.ok) throw new PortalError(503, "V3_UNAVAILABLE", "V3 服務暫時無法使用");
  try { return await response.json() as unknown; }
  catch { throw new PortalError(502, "V3_BAD_RESPONSE", "V3 服務回應格式不正確"); }
}

export function vehicleFromV3(value: unknown): VehicleRecord {
  if (!value || typeof value !== "object") throw badResponse();
  const v = value as Record<string, unknown>;
  if (!isUuid(v.id) || !isUuid(v.workshop_id) || typeof v.vin !== "string"
    || !nullableString(v.plate) || typeof v.manufacturer !== "string"
    || typeof v.model !== "string" || !nullableString(v.nickname)
    || typeof v.created_at !== "string" || typeof v.updated_at !== "string") throw badResponse();
  return {
    id: v.id, workshop_id: v.workshop_id, vin: v.vin, plate: v.plate,
    manufacturer: v.manufacturer, model: v.model, nickname: v.nickname,
    created_at: v.created_at, updated_at: v.updated_at,
  };
}

export function vehicleListFromV3(value: unknown): VehicleRecord[] {
  if (!Array.isArray(value)) throw badResponse();
  return value.map(vehicleFromV3);
}

function isUuid(value: unknown): value is string { return typeof value === "string" && UUID_RE.test(value); }
function nullableString(value: unknown): value is string | null { return value === null || typeof value === "string"; }
function badResponse(): PortalError { return new PortalError(502, "V3_BAD_RESPONSE", "V3 服務回應格式不正確"); }
