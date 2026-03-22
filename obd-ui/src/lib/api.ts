import type {
  DiagnosisHistoryResponse,
  FeedbackHistoryResponse,
  FeedbackResponse,
  OBDAnalysisResponse,
  OBDFeedbackRequest,
  RetrievalResult,
  SessionListResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
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
// Shared SSE streaming helper
// ---------------------------------------------------------------------------

type SSECallbacks = {
  onToken: (token: string) => void;
  onDone: (fullText: string) => void;
  onError: (error: string) => void;
  onStatus?: (message: string) => void;
};

async function streamSSE(url: string, cb: SSECallbacks): Promise<void> {
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

    for (const frame of frames) {
      if (!frame.trim()) continue;

      // Skip SSE comments (lines starting with ":")
      const lines = frame.split("\n").filter((l) => !l.startsWith(":"));
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

      // data is JSON-encoded string
      let parsed: string;
      try {
        parsed = JSON.parse(data);
      } catch {
        parsed = data;
      }

      switch (event) {
        case "token":
          cb.onToken(parsed);
          break;
        case "cached":
        case "done":
          cb.onDone(parsed);
          break;
        case "error":
          cb.onError(parsed);
          break;
        case "status":
          cb.onStatus?.(parsed);
          break;
      }
    }
  }
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
  onDone: (fullText: string) => void,
  onError: (error: string) => void,
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
export async function getPremiumModels(): Promise<{ models: string[]; default: string }> {
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
  onDone: (fullText: string) => void,
  onError: (error: string) => void,
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
