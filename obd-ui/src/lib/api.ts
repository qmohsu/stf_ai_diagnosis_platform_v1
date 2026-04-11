import type {
  AgentCachedEvent,
  AgentDoneEvent,
  AgentErrorEvent,
  AgentToolCallEvent,
  AgentToolResultEvent,
  DiagnosisHistoryResponse,
  FeedbackHistoryResponse,
  FeedbackResponse,
  OBDAnalysisResponse,
  OBDFeedbackRequest,
  RetrievalResult,
  SessionListResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const TOKEN_KEY = "stf_auth_token";

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------

function getAuthHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

function handle401(res: Response): void {
  if (res.status === 401 && typeof window !== "undefined") {
    localStorage.removeItem(TOKEN_KEY);
    window.location.href = "/login";
  }
}

export async function loginUser(
  username: string,
  password: string,
): Promise<{ access_token: string; token_type: string }> {
  const body = new URLSearchParams({ username, password });
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function registerUser(
  username: string,
  password: string,
): Promise<{ message: string; username: string }> {
  const res = await fetch(`${API_URL}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// OBD endpoints
// ---------------------------------------------------------------------------

export async function analyzeOBDLog(rawText: string): Promise<OBDAnalysisResponse> {
  const res = await fetch(`${API_URL}/v2/obd/analyze`, {
    method: "POST",
    body: rawText,
    headers: {
      "Content-Type": "application/octet-stream",
      ...getAuthHeaders(),
    },
  });
  handle401(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getAnalysisSession(sessionId: string): Promise<OBDAnalysisResponse> {
  const res = await fetch(`${API_URL}/v2/obd/${sessionId}`, {
    headers: getAuthHeaders(),
  });
  handle401(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * List the current user's OBD analysis sessions (paginated).
 */
export async function listSessions(
  limit?: number,
  offset?: number,
  status?: string,
): Promise<SessionListResponse> {
  const params = new URLSearchParams();
  if (limit !== undefined) params.set("limit", String(limit));
  if (offset !== undefined) params.set("offset", String(offset));
  if (status) params.set("status", status);
  const qs = params.toString();
  const res = await fetch(
    `${API_URL}/v2/obd/sessions${qs ? `?${qs}` : ""}`,
    { headers: getAuthHeaders() },
  );
  handle401(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function retrieveRAG(
  query: string,
  topK?: number,
): Promise<{ results: RetrievalResult[] }> {
  const res = await fetch(`${API_URL}/v1/rag/retrieve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify({ query, top_k: topK ?? 5 }),
  });
  handle401(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Shared SSE streaming infrastructure
// ---------------------------------------------------------------------------

interface SSEFrame {
  event: string;
  data: unknown;
}

/**
 * POST to a streaming endpoint and yield parsed SSE frames.
 *
 * Handles auth, 401 redirect, ReadableStream consumption, and
 * frame boundary parsing.  Callers provide their own dispatch
 * logic via the ``onFrame`` callback.
 */
async function consumeSSEStream(
  url: string,
  onFrame: (frame: SSEFrame) => void,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: getAuthHeaders(),
  });

  handle401(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Parse complete SSE frames from the buffer
    const frames = buffer.split("\n\n");
    // Last element may be incomplete — keep it in buffer
    buffer = frames.pop() ?? "";

    for (const raw of frames) {
      if (!raw.trim()) continue;

      // Skip SSE comments (lines starting with ":")
      const lines = raw.split("\n").filter((l) => !l.startsWith(":"));
      if (lines.length === 0) continue;

      let event = "";
      let data = "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          event = line.slice(7);
        } else if (line.startsWith("data: ")) {
          data = line.slice(6);
        }
      }

      if (!data) continue;

      // data is JSON-encoded (string or object)
      let parsed: unknown;
      try {
        parsed = JSON.parse(data);
      } catch {
        parsed = data;
      }

      onFrame({ event, data: parsed });
    }
  }
}

// ---------------------------------------------------------------------------
// V1 SSE streaming (local / premium diagnosis)
// ---------------------------------------------------------------------------

type SSECallbacks = {
  onToken: (token: string) => void;
  onDone: (fullText: string, diagnosisHistoryId: string | null) => void;
  onError: (error: string, errorCode?: string) => void;
  onStatus?: (message: string) => void;
};

async function streamSSE(url: string, cb: SSECallbacks): Promise<void> {
  return consumeSSEStream(url, ({ event, data: parsed }) => {
    switch (event) {
      case "token":
        cb.onToken(parsed as string);
        break;
      case "cached":
      case "done":
        if (typeof parsed === "object" && parsed !== null && "text" in parsed) {
          const obj = parsed as { text: string; diagnosis_history_id?: string | null };
          cb.onDone(obj.text, obj.diagnosis_history_id ?? null);
        } else {
          // Backward compatibility: plain string payload
          cb.onDone(parsed as string, null);
        }
        break;
      case "error":
        if (typeof parsed === "object" && parsed !== null && "message" in parsed) {
          const errObj = parsed as { message: string; error_code?: string };
          cb.onError(errObj.message, errObj.error_code);
        } else {
          cb.onError(parsed as string);
        }
        break;
      case "status":
        cb.onStatus?.(parsed as string);
        break;
    }
  });
}

/**
 * Stream AI diagnosis via SSE.
 *
 * @param onToken   Called for each incremental text chunk.
 * @param onDone    Called once with the full diagnosis text when generation completes.
 * @param onError   Called if the stream encounters an error.
 * @param onStatus  Called with status messages (e.g. "Initializing LLM...").
 */
export async function streamDiagnosis(
  sessionId: string,
  onToken: (token: string) => void,
  onDone: (fullText: string, diagnosisHistoryId: string | null) => void,
  onError: (error: string, errorCode?: string) => void,
  onStatus?: (message: string) => void,
  force?: boolean,
  locale?: string,
): Promise<void> {
  const params = new URLSearchParams();
  if (force) params.set("force", "true");
  if (locale) params.set("locale", locale);
  const qs = params.toString();
  const url = `${API_URL}/v2/obd/${sessionId}/diagnose${qs ? `?${qs}` : ""}`;
  return streamSSE(url, { onToken, onDone, onError, onStatus });
}

/**
 * Fetch the admin-curated list of available premium models.
 */
export async function getPremiumModels(): Promise<{ models: string[]; default: string; blocked: string[] }> {
  const res = await fetch(`${API_URL}/v2/obd/premium/models`, {
    headers: getAuthHeaders(),
  });
  handle401(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Stream premium AI diagnosis via SSE (cloud LLM).
 *
 * Same interface as streamDiagnosis but hits the /premium endpoint.
 * Accepts an optional model override to select a specific OpenRouter model.
 */
export async function streamPremiumDiagnosis(
  sessionId: string,
  onToken: (token: string) => void,
  onDone: (fullText: string, diagnosisHistoryId: string | null) => void,
  onError: (error: string, errorCode?: string) => void,
  onStatus?: (message: string) => void,
  force?: boolean,
  model?: string,
  locale?: string,
): Promise<void> {
  const params = new URLSearchParams();
  if (force) params.set("force", "true");
  if (model) params.set("model", model);
  if (locale) params.set("locale", locale);
  const qs = params.toString();
  const url = `${API_URL}/v2/obd/${sessionId}/diagnose/premium${qs ? `?${qs}` : ""}`;
  return streamSSE(url, { onToken, onDone, onError, onStatus });
}

// ---------------------------------------------------------------------------
// Agent SSE streaming
// ---------------------------------------------------------------------------

export type AgentSSECallbacks = {
  onToken: (token: string) => void;
  onToolCall: (data: AgentToolCallEvent) => void;
  onToolResult: (data: AgentToolResultEvent) => void;
  onDone: (data: AgentDoneEvent) => void;
  onError: (data: AgentErrorEvent) => void;
  onCached: (data: AgentCachedEvent) => void;
  onStatus?: (message: string) => void;
  onSessionStart?: (data: { max_iterations: number }) => void;
};

async function streamAgentSSE(
  url: string,
  cb: AgentSSECallbacks,
): Promise<void> {
  return consumeSSEStream(url, ({ event, data: parsed }) => {
    switch (event) {
      case "token":
        cb.onToken(parsed as string);
        break;
      case "tool_call":
        cb.onToolCall(parsed as AgentToolCallEvent);
        break;
      case "tool_result":
        cb.onToolResult(parsed as AgentToolResultEvent);
        break;
      case "done":
        cb.onDone(parsed as AgentDoneEvent);
        break;
      case "cached":
        cb.onCached(parsed as AgentCachedEvent);
        break;
      case "error":
        if (typeof parsed === "object" && parsed !== null && "message" in parsed) {
          cb.onError(parsed as AgentErrorEvent);
        } else {
          cb.onError({ error_type: "unknown", message: String(parsed) });
        }
        break;
      case "status":
        // session_start is mapped to "status" by the backend
        // but carries an object with max_iterations
        if (
          typeof parsed === "object" &&
          parsed !== null &&
          "max_iterations" in parsed
        ) {
          const obj = parsed as { max_iterations: number };
          cb.onSessionStart?.({ max_iterations: obj.max_iterations });
        } else {
          cb.onStatus?.(typeof parsed === "string" ? parsed : JSON.stringify(parsed));
        }
        break;
    }
  });
}

/**
 * Stream agent AI diagnosis via SSE.
 *
 * Hits POST /v2/obd/{sessionId}/diagnose/agent which runs
 * the ReAct agent loop with tool calling.
 */
export async function streamAgentDiagnosis(
  sessionId: string,
  callbacks: AgentSSECallbacks,
  options?: {
    force?: boolean;
    locale?: string;
    maxIterations?: number;
    forceAgent?: boolean;
    forceOneshot?: boolean;
  },
): Promise<void> {
  const params = new URLSearchParams();
  if (options?.force) params.set("force", "true");
  if (options?.locale) params.set("locale", options.locale);
  if (options?.maxIterations) params.set("max_iterations", String(options.maxIterations));
  if (options?.forceAgent) params.set("force_agent", "true");
  if (options?.forceOneshot) params.set("force_oneshot", "true");
  const qs = params.toString();
  const url = `${API_URL}/v2/obd/${sessionId}/diagnose/agent${qs ? `?${qs}` : ""}`;
  return streamAgentSSE(url, callbacks);
}

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------

export async function submitFeedback(
  sessionId: string,
  feedback: OBDFeedbackRequest,
  tab: "summary" | "detailed" | "rag" | "ai_diagnosis" | "premium_diagnosis",
): Promise<FeedbackResponse> {
  const res = await fetch(`${API_URL}/v2/obd/${sessionId}/feedback/${tab}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify(feedback),
  });
  handle401(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Fetch diagnosis history for a session (paginated).
 *
 * Returns past diagnosis generations ordered by created_at
 * descending.  Optionally filtered by provider.
 *
 * @param sessionId  OBD analysis session UUID.
 * @param limit      Max items to return (1-200, default 50).
 * @param offset     Number of items to skip (default 0).
 * @param provider   Optional filter: "local" or "premium".
 */
export async function getDiagnosisHistory(
  sessionId: string,
  limit?: number,
  offset?: number,
  provider?: "local" | "premium",
): Promise<DiagnosisHistoryResponse> {
  const params = new URLSearchParams();
  if (limit !== undefined) params.set("limit", String(limit));
  if (offset !== undefined) params.set("offset", String(offset));
  if (provider) params.set("provider", provider);
  const qs = params.toString();
  const res = await fetch(
    `${API_URL}/v2/obd/${sessionId}/history${qs ? `?${qs}` : ""}`,
    { headers: getAuthHeaders() },
  );
  handle401(res);
  if (!res.ok) {
    const detail = await res
      .json()
      .catch(() => ({ detail: res.statusText }));
    throw new Error(
      detail.detail || `HTTP ${res.status}`,
    );
  }
  return res.json();
}

/**
 * Fetch feedback history for a session (paginated).
 *
 * Returns all feedback across all 5 feedback types
 * ordered by created_at descending.
 *
 * @param sessionId  OBD analysis session UUID.
 * @param limit      Max items to return (1-200, default 50).
 * @param offset     Number of items to skip (default 0).
 */
export async function getFeedbackHistory(
  sessionId: string,
  limit?: number,
  offset?: number,
): Promise<FeedbackHistoryResponse> {
  const params = new URLSearchParams();
  if (limit !== undefined) params.set("limit", String(limit));
  if (offset !== undefined) params.set("offset", String(offset));
  const qs = params.toString();
  const res = await fetch(
    `${API_URL}/v2/obd/${sessionId}/feedback${qs ? `?${qs}` : ""}`,
    { headers: getAuthHeaders() },
  );
  handle401(res);
  if (!res.ok) {
    const detail = await res
      .json()
      .catch(() => ({ detail: res.statusText }));
    throw new Error(
      detail.detail || `HTTP ${res.status}`,
    );
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Audio feedback
// ---------------------------------------------------------------------------

/**
 * Upload an audio recording to staging storage.
 *
 * Returns a short-lived audio_token to include in the subsequent
 * feedback submission.
 */
export async function uploadAudio(
  blob: Blob,
): Promise<{ audio_token: string; size_bytes: number }> {
  const formData = new FormData();
  formData.append("file", blob, "recording.webm");
  const headers = getAuthHeaders();
  // Do NOT set Content-Type — let the browser set the
  // multipart boundary automatically.
  const res = await fetch(`${API_URL}/v2/obd/audio/upload`, {
    method: "POST",
    headers,
    body: formData,
  });
  handle401(res);
  if (!res.ok) {
    const detail = await res
      .json()
      .catch(() => ({ detail: res.statusText }));
    throw new Error(
      detail.detail || `HTTP ${res.status}`,
    );
  }
  return res.json();
}

/**
 * Fetch audio blob for a feedback entry (with auth).
 *
 * The backend audio endpoint requires a Bearer token which
 * cannot be set via ``<audio src>``.  This helper fetches the
 * binary via JS and returns a Blob suitable for
 * ``URL.createObjectURL()``.
 */
export async function fetchAudioBlob(
  feedbackId: string,
): Promise<Blob> {
  const res = await fetch(
    `${API_URL}/v2/obd/audio/${feedbackId}`,
    { headers: getAuthHeaders() },
  );
  handle401(res);
  if (!res.ok) {
    throw new Error(`Failed to fetch audio: HTTP ${res.status}`);
  }
  return res.blob();
}
