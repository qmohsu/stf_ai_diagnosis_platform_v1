import { accessTokenFromRequest, clearPortalSession, logoutFromV3 } from "@/server/auth-session";
import { portalError, portalJson } from "@/server/http";
import { authRateLimit } from "@/server/rate-limit";
import { requireSameOrigin } from "@/server/request-security";
import { clearVehicleContext } from "@/server/vehicle-session";

export async function POST(request: Request) {
  const limited = authRateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    const token = accessTokenFromRequest(request);
    await logoutFromV3(token);
    const response = portalJson({ loggedOut: true });
    clearPortalSession(response);
    clearVehicleContext(response);
    return response;
  } catch (error) { return portalError(error); }
}
