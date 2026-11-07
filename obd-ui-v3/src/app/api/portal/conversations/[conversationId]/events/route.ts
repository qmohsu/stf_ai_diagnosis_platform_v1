import { withAccessLog } from "@/server/access-log";
import { requirePortalSession } from "@/server/auth-session";
import { checkedAfterSeq, checkedUuid, eventsFromV3, v3PortalJson, v3PortalResponse } from "@/server/diagnosis-client";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function GETHandler(request: Request, context: { params: Promise<{ conversationId: string }> }): Promise<Response> {
  const limited = rateLimit(request); if (limited) return limited;
  let upstreamAbort: AbortController | undefined;
  let onDisconnect: (() => void) | undefined;
  try {
    const session = await requirePortalSession(request);
    const conversationId = checkedUuid((await context.params).conversationId);
    const afterSeq = checkedAfterSeq(request);
    const path = `/v3/conversations/${conversationId}/events?after_seq=${afterSeq}`;
    if (!request.headers.get("accept")?.includes("text/event-stream")) {
      const events = eventsFromV3(await v3PortalJson(path, session.token));
      return portalJson({ events });
    }
    upstreamAbort = new AbortController();
    const abortController = upstreamAbort;
    onDisconnect = () => abortController.abort();
    request.signal.addEventListener("abort", onDisconnect, { once: true });
    const upstream = await v3PortalResponse(path, session.token, {
      headers: { Accept: "text/event-stream" }, signal: abortController.signal,
    });
    if (!upstream.body || !upstream.headers.get("content-type")?.toLowerCase().startsWith("text/event-stream")) {
      throw new PortalError(502, "V3_BAD_RESPONSE", "V3 事件串流格式不正確");
    }
    const reader = upstream.body.getReader();
    const listener = onDisconnect;
    const cleanup = () => { request.signal.removeEventListener("abort", listener); };
    const body = new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          const { done, value } = await reader.read();
          if (done) { cleanup(); controller.close(); }
          else controller.enqueue(value);
        } catch (error) { cleanup(); controller.error(error); }
      },
      async cancel() { abortController.abort(); cleanup(); await reader.cancel().catch(() => undefined); },
    });
    return new Response(body, { status: 200, headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "private, no-cache, no-store, must-revalidate",
      "X-Accel-Buffering": "no",
      "X-Content-Type-Options": "nosniff",
    } });
  } catch (error) {
    upstreamAbort?.abort();
    if (onDisconnect) request.signal.removeEventListener("abort", onDisconnect);
    return portalError(error);
  }
}
export const GET = withAccessLog("/api/portal/conversations/[conversationId]/events", GETHandler);
