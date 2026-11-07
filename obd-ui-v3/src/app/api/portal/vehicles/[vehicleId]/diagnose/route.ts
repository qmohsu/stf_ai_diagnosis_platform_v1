import { withAccessLog } from "@/server/access-log";
import { requirePortalSession } from "@/server/auth-session";
import { checkedUuid, diagnosisFromV3, v3PortalJson } from "@/server/diagnosis-client";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { requireSameOrigin } from "@/server/request-security";

async function POSTHandler(request: Request, context: { params: Promise<{ vehicleId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    const session = await requirePortalSession(request);
    const vehicleId = checkedUuid((await context.params).vehicleId);
    let body: unknown;
    try { body = await request.json(); }
    catch { throw new PortalError(400, "INVALID_REQUEST", "請選擇行車日誌"); }
    if (typeof body !== "object" || body === null || Array.isArray(body) || !("obd_log_id" in body)
      || typeof body.obd_log_id !== "string") {
      throw new PortalError(400, "INVALID_REQUEST", "請選擇行車日誌");
    }
    let logId: string;
    try { logId = checkedUuid(body.obd_log_id); }
    catch { throw new PortalError(400, "INVALID_REQUEST", "行車日誌編號不正確"); }
    const diagnosis = diagnosisFromV3(await v3PortalJson(`/v3/vehicles/${vehicleId}/diagnose`, session.token, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ obd_log_id: logId }),
    }));
    return portalJson(diagnosis, { status: diagnosis.existing ? 200 : 202 });
  } catch (error) { return portalError(error); }
}

export const runtime = "nodejs";
export const POST = withAccessLog("/api/portal/vehicles/[vehicleId]/diagnose", POSTHandler);
