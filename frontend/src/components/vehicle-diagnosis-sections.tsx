"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PortalApiError, portalFetch, portalPost } from "@/lib/api-client";
import type { DiagnoseResult, DiagnosisConversation, ObdLogRecord } from "@/lib/v3-domain";
import { LogUpload } from "@/components/log-upload";

type LogPage = { logs: ObdLogRecord[]; nextOffset: number | null };
type ConversationPage = { conversations: DiagnosisConversation[]; nextOffset: number | null };
const PAGE_SIZE = 20;

export function VehicleDiagnosisSections({ vehicleId }: { vehicleId: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [logOffset, setLogOffset] = useState(0);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [existingNotice, setExistingNotice] = useState<{ requestedLogId: string; result: DiagnoseResult } | null>(null);
  const logsQuery = useQuery({
    queryKey: ["vehicle-logs", vehicleId, logOffset],
    queryFn: () => portalFetch<LogPage>(`/api/portal/vehicles/${vehicleId}/logs?offset=${logOffset}`),
  });
  const historyQuery = useQuery({
    queryKey: ["vehicle-conversations", vehicleId, historyOffset],
    queryFn: () => portalFetch<ConversationPage>(`/api/portal/vehicles/${vehicleId}/conversations?offset=${historyOffset}`),
  });
  const diagnose = useMutation({
    mutationFn: (logId: string) => portalPost<DiagnoseResult>(`/api/portal/vehicles/${vehicleId}/diagnose`, { obd_log_id: logId }),
    onMutate: () => setExistingNotice(null),
    onSuccess: (result, requestedLogId) => {
      if (result.existing) setExistingNotice({ requestedLogId, result });
      else router.push(`/vehicles/${vehicleId}/conversations/${result.conversation_id}`);
    },
  });

  return <div className="vehicle-sections">
    <section className="panel" aria-labelledby="logs-heading">
      <div className="section-heading"><div><h2 id="logs-heading">行車日誌</h2><p>每次行程後，車載設備上傳的日誌會列在這裡。</p></div></div>
      <LogUpload vehicleId={vehicleId} onStored={() => { setLogOffset(0); void queryClient.invalidateQueries({ queryKey: ["vehicle-logs", vehicleId] }); }} />
      {logsQuery.isPending && <p className="inline-state" role="status">正在載入日誌…</p>}
      {logsQuery.isError && <QueryFailure error={logsQuery.error} retry={() => void logsQuery.refetch()} label="日誌" />}
      {logsQuery.isSuccess && logsQuery.data.logs.length === 0 && <p className="inline-state">{logOffset > 0 ? "這一頁沒有日誌，請返回上一頁。" : "目前沒有已上傳的日誌。車載設備上傳後即可在這裡發起診斷。"}</p>}
      {logsQuery.isSuccess && logsQuery.data.logs.length > 0 && <ul className="record-list">{logsQuery.data.logs.map((log) => <li className="record-item" key={log.id}>
        <div className="record-copy"><strong>{log.original_filename || "行車日誌"}</strong><span>行程：{formatDate(log.recorded_start || log.recorded_end || log.uploaded_at)}</span><small>上傳：{formatDate(log.uploaded_at)} · {log.format.toUpperCase()} · {formatBytes(log.size_bytes)} · {sourceLabel(log.source)}</small></div>
        <button type="button" className="button" disabled={diagnose.isPending} onClick={() => diagnose.mutate(log.id)} aria-label={`診斷日誌 ${log.original_filename || formatDate(log.uploaded_at)}`}>{diagnose.isPending && diagnose.variables === log.id ? "正在建立…" : "一鍵診斷"}</button>
      </li>)}</ul>}
      {diagnose.isError && <p className="action-error" role="alert">{diagnose.error instanceof PortalApiError ? diagnose.error.status === 503 ? "診斷佇列暫時無法使用，請稍後重試。" : diagnose.error.status === 404 ? "日誌已不存在或不屬於此車輛，請重新載入。" : diagnose.error.message : "發起診斷失敗，請重試。"}</p>}
      {existingNotice && <div className="inline-state" role="status"><p>{existingNotice.result.obd_log_id === existingNotice.requestedLogId ? "此日誌已有進行中的診斷，接續原會話。" : "已有另一筆日誌的診斷進行中，本次未建立新診斷。"}</p><p>既有診斷使用的日誌 ID：{existingNotice.result.obd_log_id}</p><Link className="button secondary" href={`/vehicles/${vehicleId}/conversations/${existingNotice.result.conversation_id}`}>繼續查看既有診斷</Link></div>}
      {logsQuery.isSuccess && <PageControls offset={logOffset} nextOffset={logsQuery.data.nextOffset} onPage={setLogOffset} />}
    </section>
    <section className="panel" aria-labelledby="history-heading">
      <div className="section-heading"><div><h2 id="history-heading">以往診斷</h2><p>進行中的診斷可繼續查看；已完成的診斷保留報告。</p></div></div>
      {historyQuery.isPending && <p className="inline-state" role="status">正在載入診斷紀錄…</p>}
      {historyQuery.isError && <QueryFailure error={historyQuery.error} retry={() => void historyQuery.refetch()} label="診斷紀錄" />}
      {historyQuery.isSuccess && historyQuery.data.conversations.length === 0 && <p className="inline-state">{historyOffset > 0 ? "這一頁沒有診斷紀錄，請返回上一頁。" : "尚無診斷紀錄。"}</p>}
      {historyQuery.isSuccess && historyQuery.data.conversations.length > 0 && <ul className="record-list">{historyQuery.data.conversations.map((conversation) => <li className="record-item" key={conversation.id}>
        <div className="record-copy"><strong>{formatDate(conversation.created_at)}</strong><span className={`status-pill status-${conversation.status}`}>{statusLabel(conversation.status)}{conversation.report_partial ? " · 部分報告" : ""}</span><small>{conversation.error_message || `日誌 ${conversation.obd_log_id.slice(0, 8)}…`}</small></div>
        <Link className="button secondary" href={`/vehicles/${vehicleId}/conversations/${conversation.id}`}>{conversation.has_report ? "查看報告" : "查看過程"}</Link>
      </li>)}</ul>}
      {historyQuery.isSuccess && <PageControls offset={historyOffset} nextOffset={historyQuery.data.nextOffset} onPage={setHistoryOffset} />}
    </section>
  </div>;
}

function QueryFailure({ error, retry, label }: { error: Error; retry: () => void; label: string }) {
  const api = error instanceof PortalApiError ? error : null;
  return <div className="inline-state" role="alert"><p>{api?.status === 403 ? `無權查看${label}` : api?.status === 404 ? `找不到${label}` : `${label}暫時無法載入`}</p><button type="button" className="button secondary" onClick={retry}>重試</button></div>;
}

function PageControls({ offset, nextOffset, onPage }: { offset: number; nextOffset: number | null; onPage: (offset: number) => void }) {
  if (offset === 0 && nextOffset === null) return null;
  return <nav className="pagination" aria-label="列表分頁"><button className="button secondary" type="button" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}>上一頁</button><span>第 {Math.floor(offset / PAGE_SIZE) + 1} 頁</span><button className="button secondary" type="button" disabled={nextOffset === null} onClick={() => nextOffset !== null && onPage(nextOffset)}>下一頁</button></nav>;
}

function formatDate(value: string | null): string {
  if (!value) return "時間未記錄";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "時間未記錄" : new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "大小未知";
  return value < 1024 * 1024 ? `${Math.max(1, Math.ceil(value / 1024))} KB` : `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function sourceLabel(source: string): string {
  return source === "device" ? "車載設備" : source === "web" ? "網頁上傳" : source;
}

function statusLabel(status: DiagnosisConversation["status"]): string {
  return { queued: "排隊中", running: "診斷中", done: "已完成", error: "發生錯誤", cancelled: "已取消" }[status];
}
