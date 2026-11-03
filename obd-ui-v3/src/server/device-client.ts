import { PortalError } from "@/server/errors";
import type { PortalSession } from "@/server/auth-session";
import { vehicleFromV3, v3Get, UUID_RE } from "@/server/v3-client";

export interface DeviceRecord {
  id: string; vehicle_id: string; label: string; created_at: string;
  last_seen_at: string | null; revoked_at: string | null;
}

export async function requireManagerVehicle(session: PortalSession, vehicleId: string): Promise<void> {
  if (!UUID_RE.test(vehicleId)) throw new PortalError(404, "NOT_FOUND", "找不到車輛");
  const vehicle = vehicleFromV3(await v3Get(`/v3/vehicles/${vehicleId}`, session.token));
  if (!session.user.memberships.some((member) => member.workshop_id === vehicle.workshop_id && member.role === "manager")) {
    throw new PortalError(403, "ACCESS_DENIED", "只有工作坊管理員可以管理設備憑證");
  }
}

export function deviceFromV3(value: unknown): DeviceRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw bad();
  const d = value as Record<string, unknown>;
  if (typeof d.id !== "string" || !UUID_RE.test(d.id) || typeof d.vehicle_id !== "string" || !UUID_RE.test(d.vehicle_id)
    || typeof d.label !== "string" || typeof d.created_at !== "string"
    || !(d.last_seen_at === null || typeof d.last_seen_at === "string")
    || !(d.revoked_at === null || typeof d.revoked_at === "string")) throw bad();
  return { id: d.id, vehicle_id: d.vehicle_id, label: d.label,
    created_at: d.created_at, last_seen_at: d.last_seen_at, revoked_at: d.revoked_at };
}

export function devicesFromV3(value: unknown): DeviceRecord[] {
  if (!Array.isArray(value)) throw bad();
  return value.map(deviceFromV3);
}

function bad(): PortalError { return new PortalError(502, "V3_BAD_RESPONSE", "V3 服務回應格式不正確"); }
