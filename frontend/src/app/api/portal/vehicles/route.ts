import { requirePortalUser } from "@/server/auth-session";
import { portalError, portalJson } from "@/server/http";
import { vehicleDirectory } from "@/server/portal-data";
import { rateLimit } from "@/server/rate-limit";

export async function GET(request: Request) {
  const limited = rateLimit(request); if (limited) return limited;
  try { await requirePortalUser(request); return portalJson(await vehicleDirectory(new URL(request.url).searchParams.get("q") ?? "")); }
  catch (error) { return portalError(error); }
}
