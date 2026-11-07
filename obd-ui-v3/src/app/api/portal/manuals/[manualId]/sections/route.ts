import { withAccessLog } from "@/server/access-log";
import { requirePortalSession } from "@/server/auth-session";
import { checkedUuid, sectionFromV3, v3PortalJson } from "@/server/diagnosis-client";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";

async function GETHandler(request: Request, context: { params: Promise<{ manualId: string }> }) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const manualId = checkedUuid((await context.params).manualId);
    const sectionQuery = new URL(request.url).searchParams.get("section");
    if (!sectionQuery?.trim() || sectionQuery.length > 500) throw new PortalError(400, "INVALID_QUERY", "手冊章節不正確");
    const section = sectionFromV3(await v3PortalJson(`/v3/manuals/${manualId}/sections?section=${encodeURIComponent(sectionQuery)}`, session.token));
    if (section.manual_id !== manualId) throw new PortalError(502, "V3_BAD_RESPONSE", "手冊回應編號不一致");
    return portalJson({ section });
  } catch (error) { return portalError(error); }
}

export const runtime = "nodejs";
export const GET = withAccessLog("/api/portal/manuals/[manualId]/sections", GETHandler);
