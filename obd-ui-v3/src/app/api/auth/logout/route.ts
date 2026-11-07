import { withAccessLog } from "@/server/access-log";
import { accessTokenFromRequest, clearPortalSession, logoutFromV3 } from "@/server/auth-session";
import { portalError, portalJson } from "@/server/http";
import { authRateLimit } from "@/server/rate-limit";
import { requireSameOrigin } from "@/server/request-security";

async function POSTHandler(request: Request) {
  const limited = authRateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    const token = accessTokenFromRequest(request);
    await logoutFromV3(token);
    const response = portalJson({ loggedOut: true });
    clearPortalSession(response);
    response.cookies.set("portal_vehicle_context", "", { path: "/", maxAge: 0 });
    return response;
  } catch (error) { return portalError(error); }
}

export const runtime = "nodejs";
export const POST = withAccessLog("/api/auth/logout", POSTHandler);
