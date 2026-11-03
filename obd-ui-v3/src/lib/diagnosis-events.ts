import type { DiagnosisEvent } from "@/lib/v3-domain";

const MAX_PENDING_FRAME = 2_000_000;

/** Read V3's SSE frames without storing the user's diagnosis in browser storage. */
export async function readDiagnosisStream(
  response: Response,
  onEvent: (event: DiagnosisEvent) => void,
): Promise<boolean> {
  if (!response.body) throw new Error("診斷過程沒有資料");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let complete = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      pending = pending.replace(/\r\n/g, "\n");
      let boundary = pending.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = pending.slice(0, boundary);
        pending = pending.slice(boundary + 2);
        const event = parseDiagnosisFrame(frame);
        if (event) {
          onEvent(event);
          if (event.event_type === "done") complete = true;
        }
        boundary = pending.indexOf("\n\n");
      }
      if (pending.length > MAX_PENDING_FRAME) throw new Error("診斷過程的單筆資料過大");
      if (complete) break;
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  return complete;
}

function parseDiagnosisFrame(frame: string): DiagnosisEvent | null {
  const data: string[] = [];
  let frameId: string | null = null;
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const raw = separator < 0 ? "" : line.slice(separator + 1);
    const value = raw.startsWith(" ") ? raw.slice(1) : raw;
    if (field === "data") data.push(value);
    if (field === "id") frameId = value;
  }
  if (data.length === 0) return null;
  let value: unknown;
  try { value = JSON.parse(data.join("\n")); }
  catch { throw new Error("診斷過程包含無效資料"); }
  if (!value || typeof value !== "object") throw new Error("診斷過程包含無效資料");
  const item = value as Record<string, unknown>;
  if (!Number.isSafeInteger(item.seq) || Number(item.seq) < 1 || typeof item.event_type !== "string"
    || !item.payload || typeof item.payload !== "object" || Array.isArray(item.payload)
    || typeof item.created_at !== "string"
    || (frameId !== null && frameId !== String(item.seq))) {
    throw new Error("診斷過程的事件格式不正確");
  }
  return item as unknown as DiagnosisEvent;
}
