import type { OBDAnalysisResponse, OBDFeedbackRequest, FeedbackResponse, RetrievalResult } from "./types";

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
): Promise<void> {
  const res = await fetch(`${API_URL}/v2/obd/${sessionId}/diagnose`, {
    method: "POST",
    cache: "no-store",
  });

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
          onToken(parsed);
          break;
        case "cached":
        case "done":
          onDone(parsed);
          break;
        case "error":
          onError(parsed);
          break;
        case "status":
          onStatus?.(parsed);
          break;
      }
    }
  }
}

export async function submitFeedback(
  sessionId: string,
  feedback: OBDFeedbackRequest,
  tab: "summary" | "detailed" | "rag" | "ai_diagnosis",
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
