"use client";

/* eslint-disable @next/next/no-img-element -- Manual assets have no declared dimensions; the browser must use their intrinsic size. */

import type { Element, ElementContent, Root } from "hast";
import Markdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

interface MarkdownSlot {
  type: "markdownSlot";
  index: number;
}

declare module "hast" {
  interface RootContentMap { markdownSlot: MarkdownSlot }
  interface ElementContentMap { markdownSlot: MarkdownSlot }
  interface TextData { markdownSlot?: number }
}

const parseHtml = rehypeRaw({ passThrough: ["markdownSlot"] });
const sanitizeHtml = rehypeSanitize({
  tagNames: ["table", "thead", "tbody", "tr", "th", "td", "br", "img"],
  attributes: { td: ["colSpan", "rowSpan"], th: ["colSpan", "rowSpan"], img: ["src", "alt"] },
  protocols: { src: [] },
  strip: ["script", "style", "iframe"],
  allowComments: false,
});

function mapChildren(parent: Root | Element, replace: (child: ElementContent) => ElementContent) {
  for (const [index, child] of parent.children.entries()) {
    if (child.type === "element") mapChildren(child, replace);
    if (child.type !== "doctype") parent.children[index] = replace(child);
  }
}

function rehypeManualHtml() {
  return (tree: Root, file: Parameters<typeof parseHtml>[1]) => {
    function visit(parent: Root | Element) {
      for (const child of parent.children) {
        if (child.type !== "element") continue;
        // The JSX renderer translates GFM column alignment into inline CSS.
        if ((child.tagName === "td" || child.tagName === "th") && child.properties.align) {
          child.properties.dataMarkdownAlign = child.properties.align;
          delete child.properties.align;
        }
        visit(child);
      }
      if (!parent.children.some((child) => child.type === "raw")) return;

      // Parse opening and closing tags together even when Markdown paragraphs
      // occur between them. Only the Markdown parser can produce these slots.
      const markdown: Element[] = [];
      const children = parent.children.map((child) => {
        if (child.type !== "element") return child;
        const index = markdown.push(child) - 1;
        return { type: "markdownSlot", index } satisfies MarkdownSlot;
      });
      const parsed = parseHtml({ type: "root", children }, file);
      // A text node survives sanitization only if its enclosing subtree survives.
      // HAST data is internal metadata: source HTML attributes live in properties.
      mapChildren(parsed, (child) => child.type === "markdownSlot"
        ? { type: "text", value: "", data: { markdownSlot: child.index } }
        : child);
      const safe = sanitizeHtml(parsed);
      mapChildren(safe, (child) => child.type === "text" && typeof child.data?.markdownSlot === "number"
        ? markdown[child.data.markdownSlot]
        : child);
      parent.children = safe.children.filter((child) => child.type !== "doctype");
    }
    visit(tree);
  };
}

/** Model and converted-manual Markdown are data, never trusted HTML or links. */
export function SafeMarkdown({ content, manualId }: { content: string; manualId?: string }) {
  return <div className="markdown-body"><Markdown skipHtml={!manualId} remarkPlugins={[remarkGfm]} rehypePlugins={manualId ? [rehypeManualHtml] : []} components={{
    a: ({ children }) => <span className="markdown-plain-link">{children}</span>,
    table: manualId ? ({ children }) => <div className="manual-table-scroll" tabIndex={0} role="group" aria-label="手冊表格"><table>{children}</table></div> : "table",
    img: ({ src, alt }) => {
      const safe = manualId ? manualImageUrl(manualId, typeof src === "string" ? src : undefined) : null;
      return safe ? <span className="manual-image-wrap"><img src={safe} alt={alt || "維修手冊圖片"} loading="lazy" decoding="async" /></span> : <span className="manual-image-missing">[圖片未提供：{alt || "無說明"}]</span>;
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
