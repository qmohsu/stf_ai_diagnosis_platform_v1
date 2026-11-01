import { loginWithV3, setPortalSession } from "@/server/auth-session";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { authRateLimit } from "@/server/rate-limit";
import { requireSameOrigin } from "@/server/request-security";

export async function POST(request: Request) {
  const limited = authRateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    let body: unknown;
    try { body = await request.json(); }
    catch { throw new PortalError(400, "AUTH_INVALID_REQUEST", "Request body must be valid JSON"); }
    if (!isLoginInput(body)) throw new PortalError(400, "AUTH_INVALID_REQUEST", "Username and password are required");
    const result = await loginWithV3(body.username, body.password);
    const response = portalJson({ user: result.user });
    setPortalSession(response, result.token, result.expiresAt);
    response.cookies.set("portal_vehicle_context", "", { path: "/", maxAge: 0 });
    return response;
  } catch (error) { return portalError(error); }
}

function isLoginInput(value: unknown): value is { username: string; password: string } {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return typeof item.username === "string" && item.username.length > 0 && item.username.length <= 50
    && typeof item.password === "string" && item.password.length > 0 && item.password.length <= 256;
}
