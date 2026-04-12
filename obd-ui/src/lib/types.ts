// TypeScript interfaces mirroring V2 Pydantic schemas

export interface TimeRange {
  start: string;
  end: string;
  duration_seconds: number;
  sample_count: number;
}

export interface PIDStat {
  min: number;
  max: number;
  mean: number;
  latest: number;
  unit: string;
}

export interface SignalStats {
  mean: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
  p5: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  p95: number | null;
  autocorrelation_lag1: number | null;
  mean_abs_change: number | null;
  max_abs_change: number | null;
  energy: number | null;
  entropy: number | null;
  valid_count: number;
}

export interface ValueStatistics {
  stats: Record<string, SignalStats>;
  column_units: Record<string, string>;
  resample_interval_seconds: number;
}

export interface AnomalyEvent {
  time_window: [string, string];
  signals: string[];
  pattern: string;
  context: string;
  severity: "low" | "medium" | "high";
  detector: "changepoint" | "isolation_forest" | "combined";
  score: number;
}

export interface DiagnosticClue {
  rule_id: string;
  category: "statistical" | "anomaly" | "interaction" | "dtc" | "negative_evidence";
  clue: string;
  evidence: string[];
  severity: "info" | "warning" | "critical";
}

export interface LogSummaryV2 {
  vehicle_id: string;
  time_range: TimeRange;
  dtc_codes: string[];
  pid_summary: Record<string, PIDStat>;
  value_statistics: ValueStatistics;
  anomaly_events: AnomalyEvent[];
  diagnostic_clues: string[];
  clue_details: DiagnosticClue[];
}

export interface ParsedSummary {
  parse_ok: string;
  vehicle_id: string;
  time_range: string;
  dtc_codes: string;
  pid_summary: string;
  anomaly_events: string;
  diagnostic_clues: string;
  rag_query: string;
  debug: string;
}

export interface RetrievalResult {
  text: string;
  score: number;
  doc_id: string;
  source_type: string;
  section_title: string;
  chunk_index: number;
}

export interface OBDAnalysisResponse {
  session_id: string;
  status: "PENDING" | "COMPLETED" | "FAILED";
  result: LogSummaryV2 | null;
  error_message: string | null;
  parsed_summary: ParsedSummary | null;
  diagnosis_text: string | null;
  premium_diagnosis_text: string | null;
  premium_llm_enabled: boolean;
  diagnosis_history_id: string | null;
  premium_diagnosis_history_id: string | null;
}

export interface OBDFeedbackRequest {
  rating: number;
  is_helpful: boolean;
  comments?: string;
  diagnosis_history_id?: string;
  audio_token?: string;
  audio_duration_seconds?: number;
}

export interface FeedbackResponse {
  status: string;
  feedback_id: string;
}

export interface DiagnosisHistoryItem {
  id: string;
  session_id: string;
  provider: "local" | "premium";
  model_name: string;
  diagnosis_text: string;
  created_at: string;
}

export interface DiagnosisHistoryResponse {
  session_id: string;
  items: DiagnosisHistoryItem[];
  total: number;
}

export interface FeedbackHistoryItem {
  id: string;
  session_id: string;
  tab_name:
    | "summary"
    | "detailed"
    | "rag"
    | "ai_diagnosis"
    | "premium_diagnosis";
  rating: number;
  is_helpful: boolean;
  comments: string | null;
  created_at: string;
  diagnosis_history_id: string | null;
  diagnosis_model_name: string | null;
  diagnosis_created_at: string | null;
  has_audio: boolean;
  audio_duration_seconds: number | null;
}

export interface FeedbackHistoryResponse {
  session_id: string;
  items: FeedbackHistoryItem[];
  total: number;
}

export interface SessionListItem {
  session_id: string;
  vehicle_id: string | null;
  status: "PENDING" | "COMPLETED" | "FAILED";
  input_size_bytes: number;
  created_at: string;
  updated_at: string;
  has_diagnosis: boolean;
  has_premium_diagnosis: boolean;
}

export interface SessionListResponse {
  items: SessionListItem[];
  total: number;
}

// ── Agent SSE event payloads ──────────────────────────────────

export interface AgentToolCallEvent {
  name: string;
  input: Record<string, unknown>;
  iteration: number;
  tool_call_id: string;
}

export interface AgentToolResultEvent {
  name: string;
  output: string;
  duration_ms: number;
  is_error: boolean;
  iteration: number;
}

export interface AgentDoneEvent {
  text: string;
  diagnosis_history_id: string | null;
  iterations: number;
  tools_called: string[];
  autonomy_tier: number;
  autonomy_strategy: string;
  partial?: boolean;
}

export interface AgentCachedEvent {
  text: string;
  diagnosis_history_id: string | null;
}

export interface AgentErrorEvent {
  error_type: string;
  message: string;
  iteration?: number;
}

// ── Manual management ───────────────────────────────────────

export type ManualStatus =
  | "uploading"
  | "converting"
  | "ingested"
  | "failed";

export interface ManualSummary {
  id: string;
  filename: string;
  vehicle_model: string | null;
  status: ManualStatus;
  file_size_bytes: number;
  page_count: number | null;
  section_count: number | null;
  language: string | null;
  chunk_count: number | null;
  created_at: string;
  updated_at: string;
}

export interface ManualListResponse {
  items: ManualSummary[];
  total: number;
}

export interface ManualDetail extends ManualSummary {
  content: string | null;
  converter: string | null;
  error_message: string | null;
  md_file_path: string | null;
}

export interface ManualUploadResponse {
  manual_id: string;
  status: string;
  filename: string;
}

export interface ManualStatusResponse {
  status: string;
  error_message: string | null;
  page_count: number | null;
  chunk_count: number | null;
}

/** Paired tool call + result for UI rendering. */
export interface ToolInvocation {
  id: string;
  name: string;
  input: Record<string, unknown>;
  iteration: number;
  result?: {
    output: string;
    duration_ms: number;
    is_error: boolean;
  };
  status: "calling" | "done" | "error";
}
