import http from "node:http";
const profiles = [
  { plate: "UY8280", brand: "TOYOTA", model: "COASTER", model_family: "COASTER", vehicle_class: "Light Bus", fuel_type: "Diesel", powertrain_type: "ICE", parking_location: "Kowloon", section: "Operations", orders: 7, events: 9, instances: 3, quality_issues: 1, source_xlsx: "hidden.xlsx", profile_json: "{}" },
  { plate: "AB1234", vin: "JHMGK5830HX202404", brand: "HONDA", model: "ACCORD", model_family: "ACCORD", vehicle_class: "Saloon", fuel_type: "Petrol", powertrain_type: "ICE", parking_location: "Hong Kong", section: "Engineering", orders: 2, events: 3, instances: 1, quality_issues: 0, source_xlsx: "hidden.xlsx", profile_json: "{}" },
];
const bom = [
  { code: "SYS-ENG", parent_code: null, level: 0, name_zh: "引擎系統", name_en: "Engine system", evidence_state: "observed", confidence: 1 },
  { code: "CMP-FILTER", parent_code: "SYS-ENG", level: 1, name_zh: "燃油濾清器", name_en: "Fuel filter", evidence_state: "observed", confidence: .9 },
  { code: "CMP-INJECTOR", parent_code: "CMP-FILTER", level: 2, name_zh: "燃油噴射器", name_en: "Fuel injector", evidence_state: "observed", confidence: .8 },
];
const events = [{ id: 1, order_no: "WO-1001", event_type: "fault", event_subtype: "inspection", occurred_at: "2026-08-10", odometer: 120000, system_code: "SYS-ENG", component_code: "CMP-FILTER", description_zh: "歷史檢查記錄", confidence: .9, payload_json: "hidden" }];
const coverage = { windows: [{ id: 1, start_date: "2024-01-01", end_date: "2026-08-10", start_mileage: 80000, end_mileage: 120000, confidence: .9 }], gaps: [{ id: 1, start_date: "2025-01-01", end_date: "2025-02-01", gap_type: "missing_service", description: "No record" }] };
const readiness = { instances: 3, intervals: 2, observed_failures: 1, left_truncated: 1, excluded: 0, coverage_gaps: 1 };
const intervals = { time: [{ component_code: "CMP-FILTER", episode: 1, entry_date: "2024-01-01", exit_date: "2026-08-10", event_observed: 1, inclusion_status: "included", truncation_type: "left" }], mileage: [{ component_code: "CMP-FILTER", episode: 1, entry_mileage: 80000, exit_mileage: 120000, event_observed: 1, inclusion_status: "included", truncation_type: "left" }] };
const component = { profile: profiles[0], bom: { ...bom[1], child_count: 1 }, ancestry: bom.slice(0, 2), coverage, summary: { events: 1, faults: 1, actions: 0, part_events: 1, orders: 1, parts: 1, instances: 1, transitions: 1, intervals: 1, quality_issues: 1 }, events, orders: [{ order_no: "WO-1001", status: "closed", submitted_at: "2026-08-10", submit_mileage: 120000, order_type_name_zh: "維修", payload_json: "hidden" }], parts: [{ id: 1, order_no: "WO-1001", component_code: "CMP-FILTER", part_id: "P-1", part_no: "23390", part_name: "Filter", quantity: 1 }], instances: [{ id: 1, component_code: "CMP-FILTER", generation: 1, quantity: 1, installed_at: "2024-01-01", installed_mileage: 80000, current_state: "removed", confidence: .9 }], transitions: [{ id: 1, generation: 1, event_type: "part_replacement", occurred_at: "2026-08-10", from_state: "installed", to_state: "removed" }], time_intervals: intervals.time, mileage_intervals: intervals.mileage, quality: [{ id: 1, severity: "warning", issue_type: "coverage_gap", message: "Review date gap", component_code: "CMP-FILTER" }], evidence: [{ source_file: "hidden.xlsx" }], reviews: [{ status: "pending" }], build: { run: "hidden" } };
function componentPayload(profile, code) {
  if (code === "CMP-FILTER") return { ...component, profile };
  const index = bom.findIndex((item) => item.code === code);
  if (index < 0) return null;
  const node = bom[index];
  return {
    profile,
    bom: { ...node, child_count: bom.filter((item) => item.parent_code === code).length },
    ancestry: bom.slice(0, index + 1),
    coverage,
    summary: { events: 0, faults: 0, actions: 0, part_events: 0, orders: 0, parts: 0, instances: 0, transitions: 0, intervals: 0, quality_issues: 0 },
    events: [], orders: [], parts: [], instances: [], transitions: [], time_intervals: [], mileage_intervals: [], quality: [],
  };
}
function send(response, value, status = 200) { const body = JSON.stringify(value); response.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body), "cache-control": "no-store" }); response.end(body); }
const authUser = { id: "11111111-1111-1111-1111-111111111111", username: "operator", email: null, display_name: "Fleet Operator", memberships: [] };
const accessToken = jwtWithExpiration(Math.floor(Date.now() / 1000) + 12 * 60 * 60);
const server = http.createServer(async (request, response) => {
  const path = new URL(request.url, "http://127.0.0.1").pathname;
  if (path === "/v3/auth/login" && request.method === "POST") {
    const form = new URLSearchParams(await readBody(request));
    return form.get("username") === "operator" && form.get("password") === "correct horse battery staple"
      ? send(response, { access_token: accessToken, token_type: "bearer" })
      : send(response, { detail: "LOGIN_BAD_CREDENTIALS" }, 400);
  }
  if (path === "/v3/users/me" && request.method === "GET") return request.headers.authorization === `Bearer ${accessToken}` ? send(response, authUser) : send(response, { detail: "Unauthorized" }, 401);
  if (path === "/v3/auth/logout" && request.method === "POST") return send(response, {});
  if (request.method !== "GET") return send(response, { error: "read-only" }, 405);
  if (path === "/health") return send(response, { status: "ok" });
  if (path === "/api/vehicles") return send(response, profiles);
  const match = path.match(/^\/api\/vehicles\/([^/]+)\/(profile|bom|events|coverage|survival-readiness|survival-intervals|components)(?:\/([^/]+))?$/);
  if (!match) return send(response, { error: "not found" }, 404);
  const profile = profiles.find((item) => item.plate === decodeURIComponent(match[1])); if (!profile) return send(response, { error: "not found" }, 404);
  const resource = match[2]; if (resource === "profile") return send(response, profile); if (resource === "bom") return send(response, bom); if (resource === "events") return send(response, events); if (resource === "coverage") return send(response, coverage); if (resource === "survival-readiness") return send(response, readiness); if (resource === "survival-intervals") return send(response, intervals); if (resource === "components" && match[3]) { const payload = componentPayload(profile, decodeURIComponent(match[3])); return payload ? send(response, payload) : send(response, { error: "not found" }, 404); } return send(response, { error: "not found" }, 404);
});
server.listen(4010, "127.0.0.1", () => console.log("mock twin ready"));

function readBody(request) {
  return new Promise((resolve, reject) => {
    let value = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => { value += chunk; });
    request.on("end", () => resolve(value));
    request.on("error", reject);
  });
}

function jwtWithExpiration(exp) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ exp })}.signature`;
}
