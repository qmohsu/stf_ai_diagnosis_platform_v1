"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { PortalApiError, portalFetch } from "@/lib/api-client";
import type { DiagnosisConversation, DiagnosisEvent, DiagnosisReport, ReportCitation } from "@/lib/v3-domain";
import { SafeMarkdown } from "@/components/safe-markdown";
import { useDiagnosisEvents } from "@/components/use-diagnosis-events";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function ConversationView({ vehicleId, conversationId }: { vehicleId: string; conversationId: string }) {
  const [mode, setMode] = useState<"simple" | "technical">("simple");
  const conversationQuery = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => portalFetch<{ conversation: DiagnosisConversation }>(`/api/portal/conversations/${conversationId}`),
  });
  const conversation = conversationQuery.data?.conversation;
  const belongsToVehicle = conversation?.vehicle_id === vehicleId;
  const { events, state, failure, retry } = useDiagnosisEvents(belongsToVehicle ? conversation : undefined);
  const reportQuery = useQuery({
    queryKey: ["diagnosis-report", conversationId],
    queryFn: () => portalFetch<{ report: DiagnosisReport }>(`/api/portal/conversations/${conversationId}/report`),
    enabled: Boolean(belongsToVehicle && conversation?.has_report),
  });

  const loadError = conversationQuery.error instanceof PortalApiError ? conversationQuery.error : null;
  if (conversationQuery.isPending) return <main className="page-shell"><div className="state-card" role="status">正在載入診斷紀錄…</div></main>;
  if (conversationQuery.isError || !belongsToVehicle) return <main className="page-shell"><div className="state-card" role="alert"><h1>{loadError?.status === 403 ? "無權查看此診斷" : loadError?.status === 404 || !belongsToVehicle ? "找不到診斷紀錄" : "診斷紀錄暫時無法載入"}</h1><button type="button" className="button" onClick={() => void conversationQuery.refetch()}>重試</button></div></main>;
  if (!conversation) return null;

  const latestWaiting = [...events].reverse().find((event) => event.event_type === "waiting");
  const waitingMessage = typeof latestWaiting?.payload.message === "string" ? latestWaiting.payload.message : null;
  const simpleEvents = events.filter((event) => ["waiting", "session_start", "tool_call", "tool_result", "diagnosis_done", "error", "done"].includes(event.event_type));
  const visibleEvents = mode === "simple" ? simpleEvents : events;
  const final = ["done", "error", "cancelled"].includes(conversation.status);

  return <main className="page-shell">
    <Link className="back-link" href={`/vehicles/${vehicleId}`}>← 返回車輛檔案</Link>
    <div className="page-heading"><span className="eyebrow">AI 診斷</span><h1>診斷過程</h1><p>這次診斷依據日誌 {conversation.obd_log_id.slice(0, 8)}…。重新整理後，過程會從伺服器恢復。</p></div>
    <div className="diagnosis-layout">
      <section className="panel diagnosis-process" aria-labelledby="process-heading">
        <div className="section-heading"><div><h2 id="process-heading">即時進度</h2><p>{formatDate(conversation.created_at)} 開始</p></div><span className={`status-pill status-${conversation.status}`}>{statusLabel(conversation.status)}</span></div>
        <p className="process-summary" role="status" aria-live="polite">{conversation.status === "queued" && waitingMessage ? waitingMessage : conversation.status === "running" ? waitingMessage || "AI 正在分析行車資料及維修手冊…" : conversation.status === "done" ? "診斷已完成。" : conversation.error_message || (conversation.status === "cancelled" ? "診斷已取消。" : "診斷未能完成。")}</p>
        <div className="process-tabs" role="tablist" aria-label="診斷過程檢視方式"><button type="button" role="tab" aria-selected={mode === "simple"} className={mode === "simple" ? "selected" : ""} onClick={() => setMode("simple")}>簡明進度</button><button type="button" role="tab" aria-selected={mode === "technical"} className={mode === "technical" ? "selected" : ""} onClick={() => setMode("technical")}>技術細節</button></div>
        {state === "loading" && <p className="inline-state" role="status">正在從伺服器恢復診斷過程…</p>}
        {state === "reconnecting" && <p className="inline-state" role="status">連線中斷，正在續接診斷過程…{failure ? `（${failure}）` : ""}</p>}
        {state === "archived" && <p className="inline-state">診斷過程已歸檔；報告仍可查看。</p>}
        {state === "failed" && <div className="inline-state" role="alert"><p>{failure || "診斷過程暫時無法載入。"}</p><button type="button" className="button secondary" onClick={retry}>重試</button></div>}
        {visibleEvents.length > 0 && <ol className="event-timeline">{visibleEvents.map((event) => <li className="event-item" key={event.seq}><span className="event-time">{formatTime(event.created_at)}</span><div><strong>{eventLabel(event)}</strong><p>{eventDescription(event)}</p>{mode === "technical" && <TechnicalDetails event={event} />}</div></li>)}</ol>}
        {visibleEvents.length === 0 && state !== "loading" && state !== "archived" && <p className="inline-state">尚未收到過程事件，請稍候。</p>}
      </section>
      <section className="panel diagnosis-report" aria-labelledby="report-heading">
        <div className="section-heading"><div><h2 id="report-heading">診斷報告</h2><p>結論、證據與維修手冊引用</p></div></div>
        {!conversation.has_report && !final && <p className="inline-state">報告正在產生，完成後會顯示在這裡。</p>}
        {!conversation.has_report && final && <p className="inline-state">這次診斷未產生報告。{conversation.error_message || "請查看左側的過程與錯誤資訊。"}</p>}
        {conversation.has_report && reportQuery.isPending && <p className="inline-state" role="status">正在載入診斷報告…</p>}
        {conversation.has_report && reportQuery.isError && <div className="inline-state" role="alert"><p>報告暫時無法載入。</p><button type="button" className="button secondary" onClick={() => void reportQuery.refetch()}>重試</button></div>}
        {reportQuery.data?.report && <ReportBody report={reportQuery.data.report} />}
      </section>
    </div>
  </main>;
}

function ReportBody({ report }: { report: DiagnosisReport }) {
  return <div className="report-content">
    {report.partial && <div className="report-limitation" role="note"><strong>部分報告</strong><p>{stoppedReasonLabel(report.stopped_reason)}，請留意以下限制。</p></div>}
    {report.limitations.length > 0 && <ul className="limitations-list">{report.limitations.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>}
    <SafeMarkdown content={report.content_md} />
    <div className="citation-block"><h3>引用與證據</h3>{report.citations.length === 0 ? <p className="inline-state">本報告沒有可列出的引用。</p> : <ol className="citation-list">{report.citations.map((citation, index) => <CitationItem citation={citation} key={`${citation.kind}-${citation.ref}-${index}`} />)}</ol>}</div>
    <p className="report-meta">模型：{report.model} · {formatDate(report.created_at)}</p>
  </div>;
}

function CitationItem({ citation }: { citation: ReportCitation }) {
  const manual = citation.kind === "manual" ? parseManualRef(citation.ref) : null;
  return <li><div><strong>{citation.kind === "manual" ? "維修手冊" : "故障碼"}</strong><span className="citation-ref">{citation.ref}</span>{citation.source === "NO_SOURCE" && <span className="citation-unverified">未經診斷過程核實</span>}</div>{manual && <Link className="citation-link" href={`/manuals/${manual.manualId}?section=${encodeURIComponent(manual.section)}`}>查看手冊原文 →</Link>}</li>;
}

function parseManualRef(ref: string): { manualId: string; section: string } | null {
  const separator = ref.indexOf("#");
  if (separator < 0) return null;
  const manualId = ref.slice(0, separator);
  const section = ref.slice(separator + 1);
  return UUID_RE.test(manualId) && section.length > 0 && section.length <= 500 ? { manualId, section } : null;
}

function TechnicalDetails({ event }: { event: DiagnosisEvent }) {
  if (event.event_type === "reasoning") return <small>模型內部推理內容不在頁面顯示。</small>;
  const payload = event.event_type === "tool_call" ? event.payload : event.event_type === "tool_result" ? event.payload : null;
  return payload ? <details className="event-details"><summary>查看事件資料</summary><pre>{JSON.stringify(payload, null, 2)}</pre></details> : null;
}

function eventLabel(event: DiagnosisEvent): string {
  const labels: Record<string, string> = { waiting: "等待診斷", session_start: "開始分析", reasoning: "模型分析", token: "產生報告文字", tool_call: "查詢資料", tool_result: "取得查詢結果", hypothesis: "診斷假設", context_compact: "整理分析脈絡", diagnosis_done: "報告已產生", error: "診斷出現問題", done: "診斷結束" };
  return labels[event.event_type] || "診斷事件";
}

function eventDescription(event: DiagnosisEvent): string {
  const payload = event.payload;
  if (event.event_type === "waiting") return asText(payload.message) || "正在等待診斷資源…";
  if (event.event_type === "session_start") return "已接收行車日誌，開始檢查車輛資料。";
  if (event.event_type === "tool_call") {
    const tool = asText(payload.tool);
    const args = payload.args && typeof payload.args === "object" && !Array.isArray(payload.args) ? payload.args as Record<string, unknown> : {};
    if (tool === "read_manual_section") return `正在查閱維修手冊章節：${asText(args.section) || "指定章節"}`;
    if (tool === "get_manual_toc" || tool === "list_manuals" || tool === "search_manual_text") return "正在查找對應車款的維修手冊。";
    if (tool?.includes("dtc")) return "正在檢查故障碼資料。";
    return "正在分析行車資料。";
  }
  if (event.event_type === "tool_result") return payload.is_error === true ? "資料查詢遇到問題，AI 正在繼續判斷。" : "已取得資料，繼續分析。";
  if (event.event_type === "diagnosis_done") return "報告已寫入伺服器。";
  if (event.event_type === "error") return asText(payload.message) || "診斷遇到錯誤，正在確認是否有部分報告。";
  if (event.event_type === "done") return "所有診斷步驟已結束。";
  if (event.event_type === "reasoning") return "AI 正在分析資料。";
  if (event.event_type === "token") return "AI 正在整理報告內容。";
  return "AI 正在處理診斷資料。";
}

function asText(value: unknown): string | null { return typeof value === "string" && value.trim() ? value : null; }
function stoppedReasonLabel(reason: string): string {
  const labels: Record<string, string> = { complete: "診斷已完成，但報告內容不完整", timeout: "診斷時間已達上限", budget: "診斷用量已達上限", cancelled: "診斷已取消", error: "診斷遇到錯誤" };
  return labels[reason] || "診斷提前結束";
}
function statusLabel(status: DiagnosisConversation["status"]): string { return { queued: "排隊中", running: "診斷中", done: "已完成", error: "發生錯誤", cancelled: "已取消" }[status]; }
function formatDate(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? "時間未記錄" : new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short" }).format(date); }
function formatTime(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-TW", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date); }
