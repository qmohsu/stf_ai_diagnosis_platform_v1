import { requirePortalSession } from "@/server/auth-session";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { UUID_RE, v3Get, vehicleListFromV3 } from "@/server/v3-client";

export async function GET(request: Request, context: { params: Promise<{ workshopId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const { workshopId } = await context.params;
    if (!UUID_RE.test(workshopId)) throw new PortalError(404, "NOT_FOUND", "找不到工作坊");
    if (!session.user.memberships.some((membership) => membership.workshop_id === workshopId)) {
      throw new PortalError(404, "NOT_FOUND", "找不到工作坊");
    }
    const search = new URL(request.url).searchParams;
    const q = (search.get("q") ?? "").trim();
    const rawOffset = search.get("offset") ?? "0";
    if (q.length > 50 || !/^(0|[1-9][0-9]*)$/.test(rawOffset) || Number(rawOffset) > 1_000_000) {
      throw new PortalError(400, "INVALID_QUERY", "搜尋條件不正確");
    }
    const query = new URLSearchParams({ limit: "51", offset: rawOffset });
    if (q) query.set("q", q);
    const records = vehicleListFromV3(await v3Get(`/v3/workshops/${workshopId}/vehicles?${query}`, session.token));
    return portalJson({ vehicles: records.slice(0, 50), nextOffset: records.length > 50 ? Number(rawOffset) + 50 : null });
  } catch (error) { return portalError(error); }
}
