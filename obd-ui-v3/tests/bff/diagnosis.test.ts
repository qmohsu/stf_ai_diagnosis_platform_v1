import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { requirePortalSession } from "@/server/auth-session";
import { PortalError } from "@/server/errors";
import { resetRateLimitsForTests } from "@/server/rate-limit";
import { GET as listLogs } from "@/app/api/portal/vehicles/[vehicleId]/logs/route";
import { POST as diagnose } from "@/app/api/portal/vehicles/[vehicleId]/diagnose/route";
import { GET as listConversations } from "@/app/api/portal/vehicles/[vehicleId]/conversations/route";
import { GET as getConversation } from "@/app/api/portal/conversations/[conversationId]/route";
import { GET as getEvents } from "@/app/api/portal/conversations/[conversationId]/events/route";
import { GET as getReport } from "@/app/api/portal/conversations/[conversationId]/report/route";
import { GET as getSection } from "@/app/api/portal/manuals/[manualId]/sections/route";
import { GET as getAsset } from "@/app/api/portal/manuals/[manualId]/assets/[...assetPath]/route";

vi.mock("@/server/auth-session", () => ({ requirePortalSession: vi.fn() }));

const origin = "https://stf-diagnosis.dev";
const vehicleId = "00000000-0000-4000-8000-000000000001";
const logId = "44444444-4444-4444-8444-444444444444";
const conversationId = "66666666-6666-4666-8666-666666666666";
const manualId = "33333333-3333-4333-8333-333333333333";
const session = { token: "server-only-token", user: { id: "user", username: "manager", email: null, display_name: null, memberships: [] } };
const request = (path: string, init: RequestInit = {}) => new Request(`${origin}${path}`, {
  ...init, headers: { "x-real-ip": "198.51.100.92", ...init.headers },
});
const params = <T extends object>(value: T) => ({ params: Promise.resolve(value) });
const log = { id: logId, vehicle_id: vehicleId, format: "maxlog", size_bytes: 1234,
  original_filename: "drive.csv", source: "device", recorded_start: null, recorded_end: null,
  uploaded_at: "2026-10-30T00:00:00Z", sha256: "private", vin_from_log: "private" };
const conversation = { id: conversationId, vehicle_id: vehicleId, obd_log_id: logId, status: "running",
  stage: "agent", error_code: null, error_message: null, created_at: "2026-10-30T00:00:00Z",
  started_at: "2026-10-30T00:00:01Z", finished_at: null, has_report: false,
  report_partial: null, events_archived: false, messages: [{ content: "private" }] };

beforeEach(() => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("PORTAL_PUBLIC_ORIGIN", origin);
  vi.stubEnv("STF_V3_API_BASE_URL", "http://v3.internal:8002");
  vi.mocked(requirePortalSession).mockResolvedValue(session);
  resetRateLimitsForTests();
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.clearAllMocks(); });

describe("V3 diagnosis BFF", () => {
  it("lists logs and history with server token, pagination and field allowlists", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json(Array.from({ length: 21 }, () => log)))
      .mockResolvedValueOnce(Response.json([conversation]));
    vi.stubGlobal("fetch", fetch);
    const logs = await listLogs(request(`/api/portal/vehicles/${vehicleId}/logs?offset=40`), params({ vehicleId }));
    expect(logs.status).toBe(200);
    const logsBody = await logs.json();
    expect(logsBody.logs).toHaveLength(20);
    expect(logsBody.nextOffset).toBe(60);
    expect(JSON.stringify(logsBody)).not.toContain("sha256");
    expect(fetch.mock.calls[0][0]).toBe(`http://v3.internal:8002/v3/vehicles/${vehicleId}/logs?limit=21&offset=40`);
    expect(fetch.mock.calls[0][1].headers.get("Authorization")).toBe("Bearer server-only-token");
    const history = await listConversations(request(`/api/portal/vehicles/${vehicleId}/conversations`), params({ vehicleId }));
    expect(history.status).toBe(200);
    expect((await history.json()).conversations[0].messages).toBeUndefined();
    expect(history.headers.get("Cache-Control")).toContain("no-store");
  });

  it("requires same origin and a valid log ID, then returns the existing session with its actual log", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({
      conversation_id: conversationId, status: "running", existing: true, obd_log_id: logId, secret: "private",
    })); vi.stubGlobal("fetch", fetch);
    const body = JSON.stringify({ obd_log_id: logId });
    const foreign = await diagnose(request(`/api/portal/vehicles/${vehicleId}/diagnose`, {
      method: "POST", headers: { origin: "https://other.example" }, body,
    }), params({ vehicleId }));
    expect(foreign.status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
    const invalid = await diagnose(request(`/api/portal/vehicles/${vehicleId}/diagnose`, {
      method: "POST", headers: { origin }, body: JSON.stringify({ obd_log_id: "invalid" }),
    }), params({ vehicleId }));
    expect(invalid.status).toBe(400);
    const response = await diagnose(request(`/api/portal/vehicles/${vehicleId}/diagnose`, {
      method: "POST", headers: { origin }, body,
    }), params({ vehicleId }));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ conversation_id: conversationId, status: "running", existing: true, obd_log_id: logId });
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ obd_log_id: logId });
  });

  it("keeps queue failure, missing log, and missing session distinct", async () => {
    const post = () => diagnose(request(`/api/portal/vehicles/${vehicleId}/diagnose`, {
      method: "POST", headers: { origin }, body: JSON.stringify({ obd_log_id: logId }),
    }), params({ vehicleId }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "log_not_found" }), { status: 404 })));
    expect((await post()).status).toBe(404);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "queue_unavailable" }), { status: 503 })));
    expect((await post()).status).toBe(503);
    vi.mocked(requirePortalSession).mockRejectedValueOnce(new PortalError(401, "AUTH_REQUIRED", "Sign in"));
    expect((await post()).status).toBe(401);
  });

  it("returns conversation details without private model messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(conversation)));
    const response = await getConversation(request(`/api/portal/conversations/${conversationId}`), params({ conversationId }));
    expect(response.status).toBe(200);
    expect((await response.json()).conversation.messages).toBeUndefined();
  });

  it("replays typed events and distinguishes archived process from report absence", async () => {
    const event = { seq: 1, event_type: "waiting", payload: { message: "正在啟動模型" }, created_at: "2026-10-30T00:00:00Z", secret: "private" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json([event]))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: "events_archived" }), { status: 410 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: "report_not_ready" }), { status: 404 })));
    const path = `/api/portal/conversations/${conversationId}/events?after_seq=0`;
    const replay = await getEvents(request(path), params({ conversationId }));
    expect(await replay.json()).toEqual({ events: [{ seq: 1, event_type: "waiting", payload: event.payload, created_at: event.created_at }] });
    const archived = await getEvents(request(path), params({ conversationId }));
    expect(archived.status).toBe(410);
    expect((await archived.json()).error.code).toBe("EVENTS_ARCHIVED");
    const report = await getReport(request(`/api/portal/conversations/${conversationId}/report`), params({ conversationId }));
    expect(report.status).toBe(404);
    expect((await report.json()).error.code).toBe("REPORT_NOT_READY");
  });

  it("passes SSE frames without buffering, then aborts upstream when the client leaves", async () => {
    let upstreamSignal: AbortSignal | undefined;
    const source = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode(": connected\n\nevent: waiting\nid: 1\ndata: {\"seq\":1}\n\n")); },
    });
    const fetch = vi.fn().mockImplementation(async (_url: string, init: RequestInit) => {
      upstreamSignal = init.signal ?? undefined;
      return new Response(source, { headers: { "Content-Type": "text/event-stream" } });
    }); vi.stubGlobal("fetch", fetch);
    const response = await getEvents(request(`/api/portal/conversations/${conversationId}/events?after_seq=0`, {
      headers: { Accept: "text/event-stream" },
    }), params({ conversationId }));
    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toContain("text/event-stream");
    expect(response.headers.get("X-Accel-Buffering")).toBe("no");
    const reader = response.body!.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toContain("event: waiting");
    await reader.cancel();
    expect(upstreamSignal?.aborted).toBe(true);
    expect(fetch.mock.calls[0][0]).toContain(`after_seq=0`);
  });

  it("returns only report fields and preserves partial status and citations", async () => {
    const upstream = { conversation_id: conversationId, content_md: "# 結論", citations: [{ kind: "manual", ref: `${manualId}#電裝`, source: "trace", private: "secret" }],
      partial: true, stopped_reason: "budget", limitations: ["時間不足"], model: "test", created_at: "2026-10-30T00:00:00Z", private: "secret" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(upstream)));
    const response = await getReport(request(`/api/portal/conversations/${conversationId}/report`), params({ conversationId }));
    const body = await response.json();
    expect(body.report.partial).toBe(true);
    expect(body.report.citations).toEqual([{ kind: "manual", ref: `${manualId}#電裝`, source: "trace", tool: null }]);
    expect(JSON.stringify(body)).not.toContain("private");
  });

  it("serves only a whitelisted manual section and image", async () => {
    const section = { manual_id: manualId, section_id: "electric", title: "電裝", content_md: "原文", index_track: true, page_range: [2, 3], private: "secret" };
    const png = new Uint8Array([137, 80, 78, 71]);
    const fetch = vi.fn().mockResolvedValueOnce(Response.json(section))
      .mockResolvedValueOnce(new Response(png, { headers: { "Content-Type": "image/png" } }));
    vi.stubGlobal("fetch", fetch);
    const sectionResponse = await getSection(request(`/api/portal/manuals/${manualId}/sections?section=%E9%9B%BB%E8%A3%9D`), params({ manualId }));
    expect(sectionResponse.status).toBe(200);
    expect((await sectionResponse.json()).section.private).toBeUndefined();
    expect(fetch.mock.calls[0][0]).toContain("section=%E9%9B%BB%E8%A3%9D");
    const badAsset = await getAsset(request(`/api/portal/manuals/${manualId}/assets/other.png`), params({ manualId, assetPath: ["other.png"] }));
    expect(badAsset.status).toBe(404);
    const traversal = await getAsset(request(`/api/portal/manuals/${manualId}/assets/images/../secret.png`), params({ manualId, assetPath: ["images", "..", "secret.png"] }));
    expect(traversal.status).toBe(404);
    expect(fetch).toHaveBeenCalledTimes(1);
    const image = await getAsset(request(`/api/portal/manuals/${manualId}/assets/images/diagram.png`), params({ manualId, assetPath: ["images", "diagram.png"] }));
    expect(image.status).toBe(200);
    expect(image.headers.get("Content-Type")).toBe("image/png");
    expect(image.headers.get("Cache-Control")).toContain("no-store");
    expect(Array.from(new Uint8Array(await image.arrayBuffer()))).toEqual(Array.from(png));
  });

  it("preserves a manual ambiguity response instead of choosing a chapter", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "section_ambiguous" }), { status: 409 })));
    const response = await getSection(request(`/api/portal/manuals/${manualId}/sections?section=test`), params({ manualId }));
    expect(response.status).toBe(409);
    expect((await response.json()).error.code).toBe("SECTION_AMBIGUOUS");
  });
});
