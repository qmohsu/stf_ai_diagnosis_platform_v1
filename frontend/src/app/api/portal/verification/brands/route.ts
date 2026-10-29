import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { brandSuggestions } from "@/server/vehicle-verification";

export async function GET(request: Request) {
  const limited = rateLimit(request); if (limited) return limited;
  try { await requirePortalUser(request); return portalJson(await brandSuggestions(new URL(request.url).searchParams.get("q") ?? "")); }
  catch (error) { return portalError(error); }
}
import { requirePortalUser } from "@/server/auth-session";
