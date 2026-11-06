"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { PortalApiError, portalFetch } from "@/lib/api-client";
import type { ManualSection } from "@/lib/v3-domain";
import { SafeMarkdown } from "@/components/safe-markdown";

export function ManualSectionView({ manualId, section }: { manualId: string; section: string }) {
  const [raw, setRaw] = useState(false);
  const query = useQuery({
    queryKey: ["manual-section", manualId, section],
    queryFn: () => portalFetch<{ section: ManualSection }>(`/api/portal/manuals/${manualId}/sections?section=${encodeURIComponent(section)}`),
  });
  const error = query.error instanceof PortalApiError ? query.error : null;
  const ambiguous = error?.code === "SECTION_AMBIGUOUS";
  const unreadable = error?.code === "MANUAL_NOT_READABLE";
  return <main className="page-shell">
    <Link className="back-link" href="/">← 返回工作坊</Link>
    <div className="page-heading"><span className="eyebrow">維修手冊原文</span><h1>{query.data?.section.title || "手冊章節"}</h1><p>查看診斷報告所引用的原始章節內容。</p></div>
    {query.isPending && <div className="state-card" role="status">正在載入手冊原文…</div>}
    {query.isError && <div className="state-card" role="alert"><h2>{ambiguous ? "無法唯一定位手冊章節" : unreadable ? "手冊原文尚不可閱讀" : error?.status === 404 ? "找不到手冊章節" : error?.status === 403 ? "無權查看此手冊" : "手冊暫時無法載入"}</h2><p>{ambiguous ? "此引用符合多個章節，請核對手冊目錄後再選擇。" : unreadable ? "手冊可能尚在入庫中，請稍後重試。" : error?.status === 404 ? "引用的章節可能已被移除。" : "請稍後再試。"}</p><button type="button" className="button" onClick={() => void query.refetch()}>重試</button></div>}
    {query.data && <article className="panel manual-section" aria-label="手冊章節原文">
      <div className="section-heading"><div><h2>{query.data.section.title}</h2><p>章節：{query.data.section.section_id}{query.data.section.page_range ? ` · 第 ${query.data.section.page_range[0]}–${query.data.section.page_range[1]} 頁` : ""}</p></div><button type="button" className="button secondary" onClick={() => setRaw((current) => !current)}>{raw ? "排版閱讀" : "查看原始 Markdown"}</button></div>
      {raw ? <pre className="raw-markdown">{query.data.section.content_md}</pre> : <SafeMarkdown content={query.data.section.content_md} manualId={manualId} />}
    </article>}
  </main>;
}
