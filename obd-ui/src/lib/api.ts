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

export async function submitFeedback(
  sessionId: string,
  feedback: OBDFeedbackRequest,
  tab: "summary" | "detailed" | "rag",
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
