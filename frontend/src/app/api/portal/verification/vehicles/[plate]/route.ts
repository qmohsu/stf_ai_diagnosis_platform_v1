import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { verificationCandidate } from "@/server/vehicle-verification";

export async function GET(request: Request, context: { params: Promise<{ plate: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try { await requirePortalUser(request); return portalJson(await verificationCandidate(decodeURIComponent((await context.params).plate))); }
  catch (error) { return portalError(error); }
}
import { requirePortalUser } from "@/server/auth-session";
