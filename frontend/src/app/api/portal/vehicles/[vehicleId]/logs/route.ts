import { requirePortalSession } from "@/server/auth-session";
import { checkedOffset, checkedUuid, logsFromV3, v3PortalJson } from "@/server/diagnosis-client";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";

export async function GET(request: Request, context: { params: Promise<{ vehicleId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const vehicleId = checkedUuid((await context.params).vehicleId);
    const offset = checkedOffset(request);
    const logs = logsFromV3(await v3PortalJson(`/v3/vehicles/${vehicleId}/logs?limit=21&offset=${offset}`, session.token));
    return portalJson({ logs: logs.slice(0, 20), nextOffset: logs.length > 20 ? offset + 20 : null });
  } catch (error) { return portalError(error); }
}
