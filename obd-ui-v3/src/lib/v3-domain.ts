export interface VehicleRecord {
  id: string;
  workshop_id: string;
  vin: string;
  plate: string | null;
  manufacturer: string;
  model: string;
  nickname: string | null;
  created_at: string;
  updated_at: string;
}

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

export type DiagnosisStatus = "queued" | "running" | "done" | "error" | "cancelled";

export interface DiagnosisConversation {
  id: string;
  vehicle_id: string;
  obd_log_id: string;
  status: DiagnosisStatus;
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

export interface DiagnoseResult {
  conversation_id: string;
  status: DiagnosisStatus;
  existing: boolean;
  obd_log_id: string;
}

export interface DiagnosisEvent {
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ReportCitation {
  kind: "manual" | "dtc";
  ref: string;
  source: "trace" | "NO_SOURCE";
  tool: string | null;
}

export interface DiagnosisReport {
  conversation_id: string;
  content_md: string;
  citations: ReportCitation[];
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

export type Locale = "zh-TW" | "zh-CN";
