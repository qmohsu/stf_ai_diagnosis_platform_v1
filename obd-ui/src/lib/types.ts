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
}

export interface OBDFeedbackRequest {
  rating: number;
  is_helpful: boolean;
  comments?: string;
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
