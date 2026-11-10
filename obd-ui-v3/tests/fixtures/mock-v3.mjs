import http from "node:http";

const workshopId = "11111111-1111-4111-8111-111111111111";
const vinA = "JHMGK5830HX202404";
const vinB = "1HGCM82633A123456";
const vehicles = Array.from({ length: 51 }, (_, index) => ({
  id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
  workshop_id: workshopId,
  vin: index % 2 === 0 ? vinA : vinB,
  plate: `STF-${String(index + 1).padStart(3, "0")}`,
  manufacturer: index % 2 === 0 ? "Honda" : "Toyota",
  model: index % 2 === 0 ? "Civic" : "Hiace",
  nickname: index === 0 ? "測試車輛" : null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
  secret_internal: "must-not-leak",
}));
const jwt = `${Buffer.from('{"alg":"none"}').toString("base64url")}.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url")}.mock`;
const vehicleId = vehicles[0].id;
const logId = "44444444-4444-4444-8444-444444444444";
const secondLogId = "55555555-5555-4555-8555-555555555555";
const pastConversationId = "66666666-6666-4666-8666-666666666666";
const liveConversationId = "77777777-7777-4777-8777-777777777777";
const archivedConversationId = "88888888-8888-4888-8888-888888888888";
const manualId = "33333333-3333-4333-8333-333333333333";
const logs = [
  { id: logId, vehicle_id: vehicleId, format: "maxlog", size_bytes: 15360, original_filename: "first-drive.csv", source: "device", recorded_start: "2026-10-29T08:00:00Z", recorded_end: "2026-10-29T08:15:00Z", uploaded_at: "2026-10-29T08:16:00Z", sha256: "private-sha" },
  { id: secondLogId, vehicle_id: vehicleId, format: "tsv", size_bytes: 9408, original_filename: "older-drive.tsv", source: "web", recorded_start: "2026-10-28T08:00:00Z", recorded_end: "2026-10-28T08:11:00Z", uploaded_at: "2026-10-28T08:12:00Z", sha256: "private-sha" },
];
let startedAt = null;
let activeLogId = null;
const eventTemplates = [
  { delay: 0, event_type: "session_start", payload: { vehicle_id: vehicleId } },
  { delay: 150, event_type: "waiting", payload: { reason: "model_starting", message: "正在啟動診斷模型" } },
  { delay: 600, event_type: "tool_call", payload: { tool: "search_manual_text", args: { manual_id: manualId }, tool_call_id: "call-1" } },
  { delay: 800, event_type: "tool_result", payload: { tool: "search_manual_text", duration_ms: 36, is_error: false, tool_call_id: "call-1" } },
  { delay: 1000, event_type: "tool_call", payload: { tool: "get_manual_toc", args: { manual_id: manualId }, tool_call_id: "call-2" } },
  { delay: 1200, event_type: "tool_result", payload: { tool: "get_manual_toc", duration_ms: 36, is_error: false, tool_call_id: "call-2" } },
  { delay: 1400, event_type: "tool_call", payload: { tool: "read_manual_section", args: { manual_id: manualId, section: "電裝" }, tool_call_id: "call-3" } },
  { delay: 1600, event_type: "tool_result", payload: { tool: "read_manual_section", duration_ms: 36, is_error: true, tool_call_id: "call-3" } },
  { delay: 2500, event_type: "diagnosis_done", payload: { report_id: "99999999-9999-4999-8999-999999999999" } },
  { delay: 2600, event_type: "done", payload: { status: "done" } },
];
const pastEvents = [
  { seq: 1, event_type: "session_start", payload: { obd_log_id: secondLogId }, created_at: "2026-10-28T08:13:00Z" },
  { seq: 2, event_type: "done", payload: { status: "done" }, created_at: "2026-10-28T08:14:00Z" },
];

function liveEvents() {
  if (startedAt === null) return [];
  const elapsed = Date.now() - startedAt;
  return eventTemplates.flatMap((entry, index) => entry.delay > elapsed ? [] : [{
    seq: index + 1, event_type: entry.event_type,
    payload: entry.event_type === "session_start" ? { ...entry.payload, obd_log_id: activeLogId } : entry.payload,
    created_at: new Date(startedAt + entry.delay).toISOString(), secret_internal: "must-not-leak",
  }]);
}

function conversation(id) {
  if (id === pastConversationId || id === archivedConversationId) return {
    id, vehicle_id: vehicleId, obd_log_id: secondLogId, status: "done", stage: "complete",
    error_code: null, error_message: null, created_at: "2026-10-28T08:13:00Z",
    started_at: "2026-10-28T08:13:00Z", finished_at: "2026-10-28T08:14:00Z",
    has_report: true, report_partial: id === archivedConversationId, events_archived: id === archivedConversationId,
    messages: [{ secret: "must-not-leak" }],
  };
  if (id !== liveConversationId || startedAt === null) return null;
  const done = Date.now() - startedAt >= 2600;
  return {
    id, vehicle_id: vehicleId, obd_log_id: activeLogId, status: done ? "done" : "running",
    stage: done ? "complete" : "agent", error_code: null, error_message: null,
    created_at: new Date(startedAt).toISOString(), started_at: new Date(startedAt).toISOString(),
    finished_at: done ? new Date(startedAt + 2600).toISOString() : null,
    has_report: done, report_partial: done ? false : null, events_archived: false,
    messages: [{ secret: "must-not-leak" }],
  };
}

function report(id) {
  return { conversation_id: id,
    content_md: "# 診斷結論\n\n已查閱維修手冊，請檢查電裝系統。\n\n| 項目 | 結果 |\n| --- | --- |\n| 電路 | 待檢查 |",
    citations: [{ kind: "manual", ref: `${manualId}#電裝`, source: "trace", tool: "read_manual_section" }, { kind: "manual", ref: `${manualId}#未核實章節`, source: "NO_SOURCE" }, { kind: "dtc", ref: "P00AF", source: "NO_SOURCE" }],
    partial: id === archivedConversationId, stopped_reason: id === archivedConversationId ? "budget" : "complete",
    limitations: id === archivedConversationId ? ["診斷提早結束"] : [],
    model: "openai:mock-v3", created_at: "2026-10-30T00:00:00Z", secret_internal: "must-not-leak" };
}

function sendSse(req, res, id, afterSeq) {
  res.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache", "X-Accel-Buffering": "no" });
  res.write(": connected\n\n");
  let last = afterSeq;
  let heartbeat = 0;
  const interval = setInterval(() => {
    for (const event of id === pastConversationId ? pastEvents : liveEvents()) {
      if (event.seq <= last) continue;
      res.write(`event: ${event.event_type}\nid: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`);
      last = event.seq;
      if (event.event_type === "done") { clearInterval(interval); res.end(); return; }
    }
    if (++heartbeat % 10 === 0) res.write(": keepalive\n\n");
  }, 100);
  res.on("close", () => clearInterval(interval));
}

function send(res, status, data) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1:4010");
  if (url.pathname === "/health" || url.pathname === "/v3/health") return send(res, 200, { status: "ok" });
  if (url.pathname === "/v3/auth/register" && req.method === "POST") {
    let raw = ""; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    if (body.invite_code !== "TEST-INVITE") return send(res, 422, { code: "invite_code_invalid" });
    return send(res, 201, { id: "22222222-2222-4222-8222-222222222222", username: body.username, memberships: [{ workshop_id: workshopId, workshop_name: "測試工作坊", role: "technician" }] });
  }
  if (url.pathname === "/v3/auth/login" && req.method === "POST") {
    let body = ""; for await (const chunk of req) body += chunk;
    const fields = new URLSearchParams(body);
    return fields.get("username") === "manager" && fields.get("password") === "valid-password"
      ? send(res, 200, { access_token: jwt, token_type: "bearer" })
      : send(res, 400, { detail: "LOGIN_BAD_CREDENTIALS" });
  }
  if (url.pathname === "/v3/auth/logout" && req.method === "POST") { res.writeHead(204); return res.end(); }
  if (req.headers.authorization !== `Bearer ${jwt}`) return send(res, 401, { detail: "Unauthorized" });
  if (url.pathname === "/v3/users/me") return send(res, 200, { id: "22222222-2222-4222-8222-222222222222", username: "manager", email: null, display_name: "測試管理員", memberships: [{ workshop_id: workshopId, workshop_name: "測試工作坊", role: "manager" }] });
  if (url.pathname === `/v3/workshops/${workshopId}/vehicles`) {
    if (req.method === "POST") {
      for await (const chunk of req) { void chunk; }
      return send(res, 201, vehicles[0]);
    }
    const q = (url.searchParams.get("q") || "").toLowerCase();
    const offset = Number(url.searchParams.get("offset") || 0);
    const limit = Number(url.searchParams.get("limit") || 50);
    return send(res, 200, vehicles.filter((vehicle) => [vehicle.vin, vehicle.plate, vehicle.nickname].some((value) => value?.toLowerCase().includes(q))).slice(offset, offset + limit));
  }
  if (url.pathname === `/v3/vehicles/${vehicleId}/logs` && req.method === "GET") {
    const offset = Number(url.searchParams.get("offset") || 0);
    const limit = Number(url.searchParams.get("limit") || 50);
    return send(res, 200, logs.slice(offset, offset + limit));
  }
  if (url.pathname === `/v3/vehicles/${vehicleId}/logs` && req.method === "POST") {
    for await (const chunk of req) { void chunk; }
    return send(res, 201, { ...logs[0], duplicate: false });
  }
  if (url.pathname === `/v3/vehicles/${vehicleId}/devices`) {
    if (req.method === "GET") return send(res, 200, [{ id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", vehicle_id: vehicleId, label: "測試 Jetson", created_at: "2026-10-01T00:00:00Z", last_seen_at: null, revoked_at: null }]);
    if (req.method === "POST") {
      for await (const chunk of req) { void chunk; }
      return send(res, 201, { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", vehicle_id: vehicleId, label: "新設備", created_at: "2026-10-01T00:00:00Z", last_seen_at: null, revoked_at: null, token: "FAKE-ONE-TIME-TOKEN" });
    }
  }
  if (url.pathname === "/v3/devices/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" && req.method === "DELETE") { res.writeHead(204); return res.end(); }
  if (url.pathname === `/v3/vehicles/${vehicleId}/diagnose` && req.method === "POST") {
    let raw = ""; for await (const chunk of req) raw += chunk;
    let body; try { body = JSON.parse(raw); } catch { return send(res, 422, { code: "invalid_request" }); }
    if (!logs.some((log) => log.id === body.obd_log_id)) return send(res, 404, { code: "log_not_found" });
    if (startedAt !== null && Date.now() - startedAt < 2600) return send(res, 200, {
      conversation_id: liveConversationId, status: "running", existing: true, obd_log_id: activeLogId,
    });
    startedAt = Date.now(); activeLogId = body.obd_log_id;
    return send(res, 202, { conversation_id: liveConversationId, status: "queued", existing: false, obd_log_id: activeLogId });
  }
  if (url.pathname === `/v3/vehicles/${vehicleId}/conversations` && req.method === "GET") {
    const rows = [conversation(liveConversationId), conversation(pastConversationId), conversation(archivedConversationId)].filter(Boolean);
    const offset = Number(url.searchParams.get("offset") || 0);
    const limit = Number(url.searchParams.get("limit") || 20);
    return send(res, 200, rows.slice(offset, offset + limit));
  }
  const conversationMatch = url.pathname.match(/^\/v3\/conversations\/([0-9a-f-]+)(?:\/(events|report))?$/);
  if (conversationMatch) {
    const [, id, part] = conversationMatch;
    const row = conversation(id);
    if (!row) return send(res, 404, { code: "conversation_not_found" });
    if (!part) return send(res, 200, row);
    if (part === "report") return row.has_report ? send(res, 200, report(id)) : send(res, 404, { code: "report_not_ready" });
    if (row.events_archived) return send(res, 410, { code: "events_archived" });
    const afterSeq = Number(url.searchParams.get("after_seq") || 0);
    if (req.headers.accept?.includes("text/event-stream")) return sendSse(req, res, id, afterSeq);
    return send(res, 200, (id === pastConversationId ? pastEvents : liveEvents()).filter((event) => event.seq > afterSeq));
  }
  if (url.pathname === `/v3/manuals/${manualId}/sections`) {
    const section = url.searchParams.get("section");
    if (section !== "電裝" && section !== "electric") return send(res, 404, { code: "section_not_found" });
    return send(res, 200, { manual_id: manualId, section_id: "electric", title: "電裝系統",
      content_md: "# 電裝系統\n\n原文：檢查電路接頭與保險絲。\n\n![電路示意圖](images/diagram.png)",
      index_track: true, page_range: [12, 13], secret_internal: "must-not-leak" });
  }
  if (url.pathname === `/v3/manuals/${manualId}/assets/images/diagram.png`) {
    const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j8n8AAAAASUVORK5CYII=", "base64");
    res.writeHead(200, { "Content-Type": "image/png", "Cache-Control": "private, no-store" });
    return res.end(png);
  }
  const vehicle = vehicles.find((entry) => url.pathname === `/v3/vehicles/${entry.id}`);
  if (vehicle) return send(res, 200, vehicle);
  return send(res, 404, { detail: "Not found" });
});

server.listen(4010, "127.0.0.1", () => process.stdout.write("mock-v3-ready\n"));
