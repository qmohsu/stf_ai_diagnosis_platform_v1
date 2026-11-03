import { describe, expect, it } from "vitest";
import { readDiagnosisStream } from "@/lib/diagnosis-events";
import type { DiagnosisEvent } from "@/lib/v3-domain";

function sse(event: DiagnosisEvent): string {
  return `event: ${event.event_type}\nid: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`;
}

describe("V3 diagnosis SSE parser", () => {
  it("handles split UTF-8 frames, heartbeats, and an error followed by a final done", async () => {
    const events: DiagnosisEvent[] = [
      { seq: 1, event_type: "waiting", payload: { message: "正在啟動模型" }, created_at: "2026-10-30T00:00:00Z" },
      { seq: 2, event_type: "error", payload: { code: "model_stopped" }, created_at: "2026-10-30T00:00:01Z" },
      { seq: 3, event_type: "diagnosis_done", payload: { report_id: "report" }, created_at: "2026-10-30T00:00:02Z" },
      { seq: 4, event_type: "done", payload: { status: "error" }, created_at: "2026-10-30T00:00:03Z" },
    ];
    const bytes = new TextEncoder().encode(`: connected\r\n\r\n${events.map(sse).join("")}`);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (let index = 0; index < bytes.length; index += 7) controller.enqueue(bytes.slice(index, index + 7));
        controller.close();
      },
    });
    const received: DiagnosisEvent[] = [];
    expect(await readDiagnosisStream(new Response(stream), (event) => received.push(event))).toBe(true);
    expect(received.map((event) => event.seq)).toEqual([1, 2, 3, 4]);
    expect(received[0].payload.message).toBe("正在啟動模型");
  });

  it("rejects an SSE id that disagrees with the event sequence", async () => {
    const event: DiagnosisEvent = { seq: 2, event_type: "done", payload: {}, created_at: "2026-10-30T00:00:00Z" };
    const malformed = sse(event).replace("id: 2", "id: 1");
    const stream = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode(malformed)); controller.close(); } });
    await expect(readDiagnosisStream(new Response(stream), () => undefined)).rejects.toThrow("事件格式不正確");
  });
});
