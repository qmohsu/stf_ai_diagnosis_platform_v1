import { requirePortalSession } from "@/server/auth-session";
import { requireManagerVehicle, deviceFromV3, devicesFromV3 } from "@/server/device-client";
import { v3PortalJson } from "@/server/diagnosis-client";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { requireSameOrigin } from "@/server/request-security";

type Context = { params: Promise<{ vehicleId: string }> };

export async function GET(request: Request, context: Context) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const { vehicleId } = await context.params;
    await requireManagerVehicle(session, vehicleId);
    return portalJson({ devices: devicesFromV3(await v3PortalJson(`/v3/vehicles/${vehicleId}/devices`, session.token)) });
  } catch (error) { return portalError(error); }
}

export async function POST(request: Request, context: Context) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    const session = await requirePortalSession(request);
    const { vehicleId } = await context.params;
    await requireManagerVehicle(session, vehicleId);
    const body: unknown = await request.json().catch(() => null);
    if (!body || typeof body !== "object" || Array.isArray(body)
      || typeof (body as { label?: unknown }).label !== "string"
      || !(body as { label: string }).label.trim() || (body as { label: string }).label.length > 100) {
      throw new PortalError(400, "INVALID_DEVICE", "請填寫設備名稱");
    }
    const value = await v3PortalJson(`/v3/vehicles/${vehicleId}/devices`, session.token, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: (body as { label: string }).label.trim() }),
    });
    const device = deviceFromV3(value);
    const token = (value as { token?: unknown }).token;
    if (typeof token !== "string" || !token) throw new PortalError(502, "V3_BAD_RESPONSE", "V3 服務回應格式不正確");
    return portalJson({ device, token }, { status: 201 });
  } catch (error) { return portalError(error); }
}
