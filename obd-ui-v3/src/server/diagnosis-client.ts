import { PortalError } from "@/server/errors";
import { UUID_RE } from "@/server/v3-client";

export interface ObdLogRecord {
  id: string;
  vehicle_id: string;
  format: string;
  size_bytes: number;
  original_filename: string | null;
  source: string;
  recorded_start: string | null;
  recorded_end: string | null;
  uploaded_at: string;
}

export interface ConversationRecord {
  id: string;
  vehicle_id: string;
  obd_log_id: string;
  status: string;
  stage: string;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  has_report: boolean;
  report_partial: boolean | null;
  events_archived: boolean;
}

export interface DiagnosisEvent {
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface DiagnosisReport {
  conversation_id: string;
  content_md: string;
  citations: Array<{ kind: "manual" | "dtc"; ref: string; source: "trace" | "NO_SOURCE"; tool: string | null }>;
  partial: boolean;
  stopped_reason: string;
  limitations: string[];
  model: string;
  created_at: string;
}

export interface ManualSection {
  manual_id: string;
  section_id: string;
  title: string;
  content_md: string;
  index_track: boolean;
  page_range: [number, number] | null;
}

export function checkedUuid(value: string): string {
  if (!UUID_RE.test(value)) throw new PortalError(404, "NOT_FOUND", "找不到所需資料");
  return value;
}

export function checkedOffset(request: Request): number {
  const offset = new URL(request.url).searchParams.get("offset") ?? "0";
  if (!/^(0|[1-9][0-9]*)$/.test(offset) || Number(offset) > 1_000_000) {
    throw new PortalError(400, "INVALID_QUERY", "頁碼不正確");
  }
  return Number(offset);
}

export function checkedAfterSeq(request: Request): number {
  const value = new URL(request.url).searchParams.get("after_seq") ?? "0";
  if (!/^(0|[1-9][0-9]*)$/.test(value) || Number(value) > 2_147_483_647) {
    throw new PortalError(400, "INVALID_QUERY", "事件序號不正確");
  }
  return Number(value);
}

export async function v3PortalResponse(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<Response> {
  const base = process.env.STF_V3_API_BASE_URL?.trim();
  if (!base) throw new PortalError(503, "V3_CONFIG", "V3 服務尚未設定");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  let response: Response;
  try {
    response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
      ...init,
      headers,
      cache: "no-store",
      signal: init.signal ?? AbortSignal.timeout(10_000),
    });
  } catch {
    throw new PortalError(503, "V3_UNAVAILABLE", "V3 服務暫時無法使用");
  }
  if (!response.ok) throw await upstreamError(response);
  return response;
}

export async function v3PortalJson(path: string, token: string, init?: RequestInit): Promise<unknown> {
  const response = await v3PortalResponse(path, token, init);
  try { return await response.json() as unknown; }
  catch { throw badResponse(); }
}

async function upstreamError(response: Response): Promise<PortalError> {
  let code: unknown;
  try { code = (await response.json() as { code?: unknown }).code; } catch { /* optional */ }
  if (response.status === 401) return new PortalError(401, "AUTH_REQUIRED", "登入狀態已失效");
  if (response.status === 403) return new PortalError(403, "ACCESS_DENIED", "無權查看此資料");
  if (response.status === 404) {
    if (code === "report_not_ready") return new PortalError(404, "REPORT_NOT_READY", "診斷報告尚未完成");
    if (code === "report_unavailable") return new PortalError(404, "REPORT_UNAVAILABLE", "此診斷沒有可用報告");
    return new PortalError(404, "NOT_FOUND", "找不到所需資料");
  }
  if (response.status === 409) {
    if (code === "vin_exists") return new PortalError(409, "VIN_EXISTS", "這個工作坊已有相同 VIN 的車輛");
    if (code === "section_ambiguous") return new PortalError(409, "SECTION_AMBIGUOUS", "引用未能唯一定位手冊章節");
    if (code === "manual_not_readable") return new PortalError(409, "MANUAL_NOT_READABLE", "手冊原文暫時無法閱讀");
    return new PortalError(409, "V3_CONFLICT", "目前無法完成此操作");
  }
  if (response.status === 413) return new PortalError(413, "FILE_TOO_LARGE", "檔案超過 50 MB 上限");
  if (response.status === 410 && code === "events_archived") {
    return new PortalError(410, "EVENTS_ARCHIVED", "診斷過程已歸檔，報告仍可查看");
  }
  if (response.status === 422) {
    if (code === "vin_mismatch") return new PortalError(422, "VIN_MISMATCH", "日誌 VIN 與車輛檔案不符");
    if (code === "unsupported_format") return new PortalError(422, "UNSUPPORTED_FORMAT", "不支援此日誌格式");
    return new PortalError(422, "INVALID_REQUEST", "提交資料不正確");
  }
  if (response.status === 503 && code === "queue_unavailable") {
    return new PortalError(503, "QUEUE_UNAVAILABLE", "診斷佇列暫時無法使用");
  }
  return new PortalError(503, "V3_UNAVAILABLE", "V3 服務暫時無法使用");
}

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw badResponse();
  return value as Record<string, unknown>;
}

function string(value: unknown): string {
  if (typeof value !== "string") throw badResponse();
  return value;
}

function nullableString(value: unknown): string | null {
  if (value === null) return null;
  return string(value);
}

function boolean(value: unknown): boolean {
  if (typeof value !== "boolean") throw badResponse();
  return value;
}

function number(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw badResponse();
  return value;
}

function uuid(value: unknown): string {
  const parsed = string(value);
  if (!UUID_RE.test(parsed)) throw badResponse();
  return parsed;
}

function optionalString(value: unknown): string | null {
  return value === undefined || value === null ? null : string(value);
}

function optionalBoolean(value: unknown): boolean | null {
  return value === undefined || value === null ? null : boolean(value);
}

export function logsFromV3(value: unknown): ObdLogRecord[] {
  if (!Array.isArray(value)) throw badResponse();
  return value.map((entry) => {
    const v = object(entry);
    return {
      id: uuid(v.id), vehicle_id: uuid(v.vehicle_id), format: string(v.format),
      size_bytes: number(v.size_bytes), original_filename: nullableString(v.original_filename),
      source: string(v.source), recorded_start: nullableString(v.recorded_start),
      recorded_end: nullableString(v.recorded_end), uploaded_at: string(v.uploaded_at),
    };
  });
}

export function diagnosisFromV3(value: unknown): { conversation_id: string; status: string; existing: boolean; obd_log_id: string } {
  const v = object(value);
  return { conversation_id: uuid(v.conversation_id), status: string(v.status), existing: boolean(v.existing), obd_log_id: uuid(v.obd_log_id) };
}

export function conversationFromV3(value: unknown): ConversationRecord {
  const v = object(value);
  return {
    id: uuid(v.id), vehicle_id: uuid(v.vehicle_id), obd_log_id: uuid(v.obd_log_id),
    status: string(v.status), stage: string(v.stage), error_code: optionalString(v.error_code),
    error_message: optionalString(v.error_message), created_at: string(v.created_at),
    started_at: optionalString(v.started_at), finished_at: optionalString(v.finished_at),
    has_report: boolean(v.has_report), report_partial: optionalBoolean(v.report_partial),
    events_archived: boolean(v.events_archived),
  };
}

export function conversationsFromV3(value: unknown): ConversationRecord[] {
  if (!Array.isArray(value)) throw badResponse();
  return value.map(conversationFromV3);
}

export function eventsFromV3(value: unknown): DiagnosisEvent[] {
  if (!Array.isArray(value)) throw badResponse();
  return value.map((entry) => {
    const v = object(entry);
    const seq = number(v.seq);
    if (!Number.isSafeInteger(seq) || seq < 1) throw badResponse();
    return { seq, event_type: string(v.event_type), payload: object(v.payload), created_at: string(v.created_at) };
  });
}

export function reportFromV3(value: unknown): DiagnosisReport {
  const v = object(value);
  if (!Array.isArray(v.citations) || !Array.isArray(v.limitations)) throw badResponse();
  return {
    conversation_id: uuid(v.conversation_id), content_md: string(v.content_md),
    citations: v.citations.map((entry) => {
      const citation = object(entry);
      if (citation.kind !== "manual" && citation.kind !== "dtc") throw badResponse();
      if (citation.source !== "trace" && citation.source !== "NO_SOURCE") throw badResponse();
      return { kind: citation.kind, ref: string(citation.ref), source: citation.source,
        tool: optionalString(citation.tool) };
    }),
    partial: boolean(v.partial), stopped_reason: string(v.stopped_reason),
    limitations: v.limitations.map(string), model: string(v.model), created_at: string(v.created_at),
  };
}

export function sectionFromV3(value: unknown): ManualSection {
  const v = object(value);
  let pageRange: [number, number] | null = null;
  if (v.page_range !== null && v.page_range !== undefined) {
    if (!Array.isArray(v.page_range) || v.page_range.length !== 2) throw badResponse();
    pageRange = [number(v.page_range[0]), number(v.page_range[1])];
  }
  return {
    manual_id: uuid(v.manual_id), section_id: string(v.section_id), title: string(v.title),
    content_md: string(v.content_md), index_track: boolean(v.index_track), page_range: pageRange,
  };
}

function badResponse(): PortalError {
  return new PortalError(502, "V3_BAD_RESPONSE", "V3 服務回應格式不正確");
}
