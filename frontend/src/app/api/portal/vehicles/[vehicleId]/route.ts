import { requirePortalSession } from "@/server/auth-session";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { UUID_RE, v3Get, vehicleFromV3 } from "@/server/v3-client";

export async function GET(request: Request, context: { params: Promise<{ vehicleId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const { vehicleId } = await context.params;
    if (!UUID_RE.test(vehicleId)) throw new PortalError(404, "NOT_FOUND", "找不到車輛");
    const vehicle = vehicleFromV3(await v3Get(`/v3/vehicles/${vehicleId}`, session.token));
    if (!session.user.memberships.some((membership) => membership.workshop_id === vehicle.workshop_id)) {
      throw new PortalError(404, "NOT_FOUND", "找不到車輛");
    }
    return portalJson({ vehicle });
  } catch (error) { return portalError(error); }
}
