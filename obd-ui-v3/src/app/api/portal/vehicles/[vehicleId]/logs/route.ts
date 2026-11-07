import { withAccessLog } from "@/server/access-log";
import { requirePortalSession } from "@/server/auth-session";
import { checkedOffset, checkedUuid, logsFromV3, v3PortalJson } from "@/server/diagnosis-client";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { requireSameOrigin } from "@/server/request-security";
import { PortalError } from "@/server/errors";

async function GETHandler(request: Request, context: { params: Promise<{ vehicleId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const vehicleId = checkedUuid((await context.params).vehicleId);
    const offset = checkedOffset(request);
    const logs = logsFromV3(await v3PortalJson(`/v3/vehicles/${vehicleId}/logs?limit=21&offset=${offset}`, session.token));
    return portalJson({ logs: logs.slice(0, 20), nextOffset: logs.length > 20 ? offset + 20 : null });
  } catch (error) { return portalError(error); }
}

async function POSTHandler(request: Request, context: { params: Promise<{ vehicleId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    const session = await requirePortalSession(request);
    const vehicleId = checkedUuid((await context.params).vehicleId);
    const type = request.headers.get("content-type") || "";
    if (!/^multipart\/form-data;\s*boundary=/i.test(type) || !request.body) {
      throw new PortalError(400, "INVALID_UPLOAD", "請選擇行車日誌檔案");
    }
    const length = Number(request.headers.get("content-length"));
    if (length > 50 * 1024 * 1024 + 64 * 1024) throw new PortalError(413, "FILE_TOO_LARGE", "檔案超過 50 MB 上限");
    const upstream = await v3PortalJson(`/v3/vehicles/${vehicleId}/logs`, session.token, {
      method: "POST", headers: { "Content-Type": type }, body: request.body,
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(10 * 60_000)]),
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    if (!upstream || typeof upstream !== "object" || Array.isArray(upstream) || typeof (upstream as { duplicate?: unknown }).duplicate !== "boolean") {
      throw new PortalError(502, "V3_BAD_RESPONSE", "V3 服務回應格式不正確");
    }
    const log = logsFromV3([upstream])[0];
    return portalJson({ log, duplicate: (upstream as { duplicate: boolean }).duplicate }, { status: (upstream as { duplicate: boolean }).duplicate ? 200 : 201 });
  } catch (error) { return portalError(error); }
}

export const runtime = "nodejs";
export const GET = withAccessLog("/api/portal/vehicles/[vehicleId]/logs", GETHandler);
export const POST = withAccessLog("/api/portal/vehicles/[vehicleId]/logs", POSTHandler);
