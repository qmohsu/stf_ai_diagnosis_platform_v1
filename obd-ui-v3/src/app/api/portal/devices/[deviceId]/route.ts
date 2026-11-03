import { requirePortalSession } from "@/server/auth-session";
import { checkedUuid, v3PortalResponse } from "@/server/diagnosis-client";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { requireSameOrigin } from "@/server/request-security";

export async function DELETE(request: Request, context: { params: Promise<{ deviceId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    const session = await requirePortalSession(request);
    const deviceId = checkedUuid((await context.params).deviceId);
    await v3PortalResponse(`/v3/devices/${deviceId}`, session.token, { method: "DELETE" });
    return portalJson({ revoked: true });
  } catch (error) { return portalError(error); }
}
