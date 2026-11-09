"use client";

/* eslint-disable @next/next/no-img-element -- Manual assets have no declared dimensions; the browser must use their intrinsic size. */

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Model and converted-manual Markdown are data, never trusted HTML or links. */
export function SafeMarkdown({ content, manualId }: { content: string; manualId?: string }) {
  return <div className="markdown-body"><Markdown skipHtml remarkPlugins={[remarkGfm]} components={{
    a: ({ children }) => <span className="markdown-plain-link">{children}</span>,
    img: ({ src, alt }) => {
      const safe = manualId ? manualImageUrl(manualId, typeof src === "string" ? src : undefined) : null;
      return safe ? <span className="manual-image-wrap"><img src={safe} alt={alt || "維修手冊圖片"} style={{ width: "auto", height: "auto", maxWidth: "100%" }} /></span> : <span className="manual-image-missing">[圖片未提供：{alt || "無說明"}]</span>;
    },
  }}>{content}</Markdown></div>;
}

function manualImageUrl(manualId: string, raw: string | undefined): string | null {
  if (!raw || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(manualId)
    || !raw.startsWith("images/") || /[%?#\\\u0000-\u001f]/.test(raw)) return null;
  const segments = raw.split("/");
  if (segments.length < 2 || segments.some((segment) => !segment || segment === "." || segment === "..")) return null;
  return `/api/portal/manuals/${manualId}/assets/${segments.map(encodeURIComponent).join("/")}`;
}
