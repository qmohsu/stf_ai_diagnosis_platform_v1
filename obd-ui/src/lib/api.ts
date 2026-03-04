import type {
  DiagnosisHistoryResponse,
  FeedbackResponse,
  OBDAnalysisResponse,
  OBDFeedbackRequest,
  RetrievalResult,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export async function analyzeOBDLog(rawText: string): Promise<OBDAnalysisResponse> {
  const res = await fetch(`${API_URL}/v2/obd/analyze`, {
    method: "POST",
    body: rawText,
    headers: { "Content-Type": "application/octet-stream" },
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getAnalysisSession(sessionId: string): Promise<OBDAnalysisResponse> {
  const res = await fetch(`${API_URL}/v2/obd/${sessionId}`);
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, top_k: topK ?? 5 }),
  });
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
  const res = await fetch(url, { method: "POST", cache: "no-store" });

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
): Promise<void> {
  const url = force
    ? `${API_URL}/v2/obd/${sessionId}/diagnose?force=true`
    : `${API_URL}/v2/obd/${sessionId}/diagnose`;
  return streamSSE(url, { onToken, onDone, onError, onStatus });
}

/**
 * Fetch the admin-curated list of available premium models.
 */
export async function getPremiumModels(): Promise<{ models: string[]; default: string }> {
  const res = await fetch(`${API_URL}/v2/obd/premium/models`);
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
): Promise<void> {
  const params = new URLSearchParams();
  if (force) params.set("force", "true");
  if (model) params.set("model", model);
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(feedback),
  });
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
 * @param limit      Max items to return (1–200, default 50).
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
  );
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
