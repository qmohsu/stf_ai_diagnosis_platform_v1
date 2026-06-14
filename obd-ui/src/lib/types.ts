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
  provider: "local" | "premium" | "agent";
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
    | "premium_diagnosis"
    | "agent_diagnosis";
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

export interface AgentReasoningEvent {
  text: string;
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
  | "chunking"
  | "embedding"
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
  pages_processed: number | null;
  pages_total: number | null;
  /** Current marker-pdf stage label (e.g. "Layout", "OCR"). */
  pages_phase: string | null;
  /** LLM-degradation events captured during conversion. */
  warnings: ManualWarning[] | null;
  created_at: string;
  updated_at: string;
}

export interface ManualWarning {
  event: string;
  logger?: string;
  level?: string;
  message?: string;
  ts?: string;
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
  pages_processed: number | null;
  pages_total: number | null;
  pages_phase: string | null;
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

// -----------------------------------------------------------
// Golden review dashboard (HARNESS-17 / Issue #82)
// -----------------------------------------------------------

// -----------------------------------------------------------
// HARNESS-21 [2b/4]: lane discriminator + OBD-side types
// -----------------------------------------------------------

export type GoldenLane = "manual" | "obd";

/** Buckets for the manual-agent eval lane (HARNESS-14). */
export type ManualGoldenBucket =
  | "lookup"
  | "procedural"
  | "cross-section"
  | "image-required"
  | "adversarial";

/** Buckets for the OBD-agent eval lane (HARNESS-21). */
export type OBDGoldenBucket =
  | "signal_statistics"
  | "event_finding"
  | "dtc_enumeration"
  | "dtc_decode"
  | "compound_obd"
  | "adversarial_obd";

/** Union of all bucket values across both lanes. Use the
 *  lane-specific types above when the lane is known statically. */
export type GoldenBucket = ManualGoldenBucket | OBDGoldenBucket;

export const MANUAL_BUCKETS: ManualGoldenBucket[] = [
  "lookup",
  "procedural",
  "cross-section",
  "image-required",
  "adversarial",
];

export const OBD_BUCKETS: OBDGoldenBucket[] = [
  "signal_statistics",
  "event_finding",
  "dtc_enumeration",
  "dtc_decode",
  "compound_obd",
  "adversarial_obd",
];

/** OBD-side expected citation: a signal name with optional
 *  stat / value / units / time_range pinning.  The OBD eval
 *  framework grades against these in metrics_obd.py; the
 *  /goldens/obd detail page renders them as a table beside
 *  the question. */
export interface ExpectedSignalCitation {
  signal: string;
  stat?: string | null;
  value?: number | null;
  value_tolerance_rel?: number | null;
  units?: string | null;
  time_range?: [string, string] | null;
}

/** OBD-side expected DTC: a code + optional status / ECU. */
export interface ExpectedDTC {
  code: string;
  status?: "stored" | "pending" | null;
  ecu?: string | null;
}

export type GoldenDifficulty = "easy" | "medium" | "hard";

export type GoldenReviewStatus =
  | "draft"
  | "accept"
  | "needs_revision"
  | "reject";

export interface GoldenCitation {
  manual_id: string;
  slug: string;
  quote: string;
  /** Manual-relative paths to figures (e.g. "images/<uuid>/_page_X_Picture_Y.jpeg")
   *  that visually support the quote.  Resolved against the manual's
   *  md_file_path directory by the QuestionCard renderer.  Empty list
   *  = no images attached. */
  figure_image_paths?: string[];
}

export interface GoldenEntrySummary {
  id: string;
  manual_id: string;
  category: string;
  question_type: GoldenBucket;
  difficulty: GoldenDifficulty;
  requires_image: boolean;
  question_en: string;
  question_zh: string | null;
  has_zh: boolean;
  /** HARNESS-21 [2b/4]: lane discriminator. Drives whether the
   *  /goldens/manual or /goldens/obd route surfaces this entry. */
  lane: GoldenLane;
  /** HARNESS-20: true when this entry has been promoted into
   *  the locked tier via scripts/promote_golden.py. */
  is_locked: boolean;
  /** Team's most-recent review (across ALL reviewers). */
  latest_review_status: GoldenReviewStatus | null;
  latest_review_star: number | null;
  latest_reviewer_username: string | null;
  latest_review_at: string | null;
  review_count: number;
}

export interface GoldenReviewOut {
  id: string;
  golden_entry_id: string;
  reviewer_id: string;
  star_rating: number | null;
  question_realism_score: number | null;
  answer_correctness_score: number | null;
  citation_faithfulness_score: number | null;
  status: GoldenReviewStatus;
  notes: string | null;
  has_audio: boolean;
  audio_duration_seconds: number | null;
  created_at: string;
  updated_at: string;
}

export interface GoldenEntryDetail {
  id: string;
  manual_id: string;
  category: string;
  question_type: GoldenBucket;
  difficulty: GoldenDifficulty;
  requires_image: boolean;
  question_en: string;
  question_zh: string | null;
  obd_context: string | null;
  golden_summary_en: string;
  golden_summary_zh: string | null;
  golden_citations: GoldenCitation[];
  notes: string | null;
  /** Relative path to the manual's markdown file
   *  (e.g. "<manual_slug>/manual.md"). The QuestionCard uses
   *  the parent directory as the base for resolving
   *  figure_image_paths against the nginx /manuals/data/
   *  alias. Null when the manual has been deleted or the
   *  entry references a sentinel manual_id. */
  md_file_path: string | null;
  /** HARNESS-20 lock-state flag. */
  is_locked: boolean;
  /** HARNESS-21 [2b/4]: lane + OBD-specific fields.
   *  All present for both lanes; OBD fields are empty/false
   *  for manual entries. */
  lane: GoldenLane;
  expected_signal_citations: ExpectedSignalCitation[];
  expected_dtcs: ExpectedDTC[];
  expected_no_evidence: boolean;
  pitfall_directives: string[];
}

/** Yamaha reference-stats sidecar payload served by
 *  `GET /v2/goldens/obd/reference-stats`.  Generated offline by
 *  scripts/compute_yamaha_reference.py from the canonical fixture.
 *  Used by the /goldens/obd detail page sparkline renderer. */
export interface YamahaSignalStats {
  samples_valid: number;
  min: number;
  min_at: string;
  max: number;
  max_at: string;
  mean: number;
  p50: number;
  p95: number;
  std: number;
}

export interface YamahaEventWindow {
  signal: string;
  op: string;
  threshold: number;
  ranges: [string, string][];
}

export interface YamahaReferenceStats {
  schema_version: number;
  fixture: {
    name: string;
    sha256: string;
    rows: number;
    columns: number;
    channels_present: string[];
    format: string;
  };
  signal_stats: Record<string, YamahaSignalStats>;
  event_windows: YamahaEventWindow[];
  metadata_dtcs: { code: string; status: string; ecu: string }[];
}

export interface GoldenListResponse {
  items: GoldenEntrySummary[];
  total: number;
}

export interface GoldenReviewSubmitRequest {
  star_rating: number | null;
  question_realism_score: number | null;
  answer_correctness_score: number | null;
  citation_faithfulness_score: number | null;
  status: GoldenReviewStatus;
  notes: string | null;
  audio_token: string | null;
  audio_duration_seconds: number | null;
}

// -----------------------------------------------------------
// Team feedback (HARNESS-17 Phase 2 — full transparency)
// -----------------------------------------------------------

export interface TeamReviewItem {
  review_id: string;
  reviewer_id: string;
  reviewer_username: string;
  star_rating: number | null;
  question_realism_score: number | null;
  answer_correctness_score: number | null;
  citation_faithfulness_score: number | null;
  status: GoldenReviewStatus;
  notes: string | null;
  has_audio: boolean;
  audio_duration_seconds: number | null;
  /** Snapshot of the entry's Q+A at the time this review was
   *  submitted.  Null for pre-Phase-2 reviews; UI should fall
   *  back to the live entry's text in that case. */
  snapshot_question_en: string | null;
  snapshot_question_zh: string | null;
  snapshot_summary_en: string | null;
  snapshot_summary_zh: string | null;
  snapshot_citations: GoldenCitation[] | null;
  created_at: string;
  updated_at: string;
}

export interface TeamReviewListResponse {
  items: TeamReviewItem[];
  total: number;
}
