import { setAccessVehicleId, withAccessLog } from "@/server/access-log";
import { requirePortalSession } from "@/server/auth-session";
import { checkedUuid, conversationFromV3, v3PortalJson } from "@/server/diagnosis-client";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";

async function GETHandler(request: Request, context: { params: Promise<{ conversationId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const conversationId = checkedUuid((await context.params).conversationId);
    const conversation = conversationFromV3(await v3PortalJson(`/v3/conversations/${conversationId}`, session.token));
    setAccessVehicleId(conversation.vehicle_id);
    return portalJson({ conversation });
  } catch (error) { return portalError(error); }
}

export const runtime = "nodejs";
export const GET = withAccessLog("/api/portal/conversations/[conversationId]", GETHandler);
