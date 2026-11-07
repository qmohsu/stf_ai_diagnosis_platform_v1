import { withAccessLog } from "@/server/access-log";
import { requirePortalSession } from "@/server/auth-session";
import { checkedOffset, checkedUuid, conversationsFromV3, v3PortalJson } from "@/server/diagnosis-client";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";

async function GETHandler(request: Request, context: { params: Promise<{ vehicleId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const vehicleId = checkedUuid((await context.params).vehicleId);
    const offset = checkedOffset(request);
    const conversations = conversationsFromV3(await v3PortalJson(`/v3/vehicles/${vehicleId}/conversations?limit=21&offset=${offset}`, session.token));
    return portalJson({ conversations: conversations.slice(0, 20), nextOffset: conversations.length > 20 ? offset + 20 : null });
  } catch (error) { return portalError(error); }
}

export const runtime = "nodejs";
export const GET = withAccessLog("/api/portal/vehicles/[vehicleId]/conversations", GETHandler);
