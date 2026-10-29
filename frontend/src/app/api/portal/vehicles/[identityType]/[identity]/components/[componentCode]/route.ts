import type { IdentityType } from "@/lib/domain";
import { requirePortalUser } from "@/server/auth-session";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { componentDetail } from "@/server/portal-data";
import { rateLimit } from "@/server/rate-limit";
import { requireVehicleContext } from "@/server/vehicle-session";

export async function GET(request: Request, context: { params: Promise<{ identityType: string; identity: string; componentCode: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    await requirePortalUser(request);
    const { identityType, identity, componentCode } = await context.params;
    if (identityType !== "plate" && identityType !== "vin") throw new PortalError(404, "ROUTE_NOT_FOUND", "Route not found");
    const decodedIdentity = decodeURIComponent(identity);
    await requireVehicleContext(identityType as IdentityType, decodedIdentity);
    return portalJson(await componentDetail(identityType as IdentityType, decodedIdentity, decodeURIComponent(componentCode)));
  } catch (error) { return portalError(error); }
}
