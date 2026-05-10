"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, BookOpen, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { getManual } from "@/lib/api";
import { slugify } from "@/lib/slugify";
import type { ManualDetail } from "@/lib/types";

/**
 * Recursively extract plain text from a React node tree.
 *
 * Used by the auto-id heading renderers so we can compute a
 * slug from the heading content even when marker-pdf has
 * embedded HTML page-anchor spans inside the heading.  Without
 * this, `String(children)` would yield "[object Object]" for
 * any heading containing nested elements.
 */
function extractText(children: ReactNode): string {
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) {
    return children.map(extractText).join("");
  }
  if (
    children &&
    typeof children === "object" &&
    "props" in children
  ) {
    return extractText(
      (children as { props: { children?: ReactNode } }).props
        .children,
    );
  }
  return "";
}


/**
 * Like `extractText`, but additionally strips raw HTML tag
 * patterns that survive markdown rendering when `rehype-raw`
 * is not enabled.
 *
 * marker-pdf emits headings with embedded page-anchor spans
 * (e.g. ``### <span id="page-281-1"></span>故障代碼編號 P0117、P0118``).
 * Without rehype-raw the literal `<span ...>` text reaches
 * `extractText` and would corrupt the slugify output, e.g.:
 *   "<span id="page-281-1"></span>故障代碼編號 P0117、P0118"
 * → "span-id-page-281-1-span-故障代碼編號-p0117、p0118"
 * which obviously doesn't match the citation slug.
 *
 * Strip `<...>` patterns first, then collapse whitespace,
 * before passing to slugify.
 */
function extractTextForSlug(children: ReactNode): string {
  return extractText(children)
    .replace(/<[^>]*>/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

interface ManualViewerProps {
  manualId: string;
  onBack: () => void;
}

/**
 * Strip YAML frontmatter from markdown content.
 *
 * Frontmatter is the block between the first `---` and the
 * next `---` at the start of the file.
 */
function stripFrontmatter(md: string): string {
  if (!md.startsWith("---")) return md;
  const end = md.indexOf("---", 3);
  if (end === -1) return md;
  return md.slice(end + 3).trimStart();
}

/**
 * Clean marker-pdf pagination markers from markdown.
 *
 * marker-pdf with ``paginate_output=True`` inserts lines like
 * ``{0}------------------------------------------------`` between
 * pages.  These break react-markdown parsing (the dashes are
 * interpreted as setext heading underlines).  Replace them with
 * a simple horizontal rule.
 */
function cleanPageMarkers(md: string): string {
  return md.replace(
    /\{(\d+)\}-{3,}\n/g,
    "\n---\n\n",
  );
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export function ManualViewer({ manualId, onBack }: ManualViewerProps) {
  const { t } = useTranslation();
  const [manual, setManual] = useState<ManualDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getManual(manualId)
      .then((data) => {
        if (!cancelled) setManual(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [manualId]);

  // After the markdown body renders, scroll to the URL hash if
  // present.  Citations on the golden-review dashboard link
  // here as `/manuals/<id>#<slug>`; the heading renderers below
  // emit `id` attributes matching the same slugs.
  //
  // ReactMarkdown rendering of a 400-page (~500K-char) Chinese
  // service manual can take many seconds.  We poll rather than
  // fire a single timeout, and the budget is generous (15s) so
  // slow machines don't lose the deep-link.  Per-attempt log
  // lines and a final diagnostic dump make it possible to debug
  // any remaining slug mismatch directly from the user's
  // browser console without having to add custom JS.
  useEffect(() => {
    if (!manual?.content) return;
    if (typeof window === "undefined") return;
    const hash = window.location.hash;
    if (!hash) return;
    const targetId = decodeURIComponent(hash.slice(1));
    if (!targetId) return;
    console.log(
      `[ManualViewer] deep-link target requested: "${targetId}"`,
    );
    let attempts = 0;
    const maxAttempts = 75; // 75 × 200ms = 15s
    const interval = window.setInterval(() => {
      attempts += 1;
      const el = document.getElementById(targetId);
      if (el) {
        window.clearInterval(interval);
        console.log(
          `[ManualViewer] deep-link target found at attempt ${attempts} (${
            attempts * 200
          }ms)`,
          el,
        );
        el.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
        el.classList.add("ring-2", "ring-primary", "rounded");
        window.setTimeout(() => {
          el.classList.remove("ring-2", "ring-primary", "rounded");
        }, 2400);
      } else if (attempts >= maxAttempts) {
        window.clearInterval(interval);
        // Diagnostic dump: list every heading ID actually
        // present in the DOM so we can compare against the
        // requested slug and see exactly where the drift is.
        const allHeadings = Array.from(
          document.querySelectorAll("h1, h2, h3, h4, h5, h6"),
        );
        const allIds = allHeadings
          .map((h) => h.id)
          .filter(Boolean);
        console.warn(
          `[ManualViewer] deep-link target NOT FOUND after ${
            attempts * 200
          }ms: "${targetId}". Total headings: ${
            allHeadings.length
          }. Headings WITH ids: ${allIds.length}.`,
        );
        // Surface a small sample so we don't blow up the
        // console — full list available via the all-id dump.
        console.warn(
          "[ManualViewer] First 10 heading ids found:",
          allIds.slice(0, 10),
        );
        // Try a fuzzy match — substring of the slug — just
        // for debugging.  Helps spot prefix/encoding drift.
        const fuzzyHits = allIds.filter((id) =>
          id.includes(targetId.slice(0, 6)),
        );
        if (fuzzyHits.length) {
          console.warn(
            "[ManualViewer] Fuzzy matches (heading id contains first 6 chars of target):",
            fuzzyHits,
          );
        }
      } else if (attempts === 5 || attempts === 25) {
        // Mid-poll progress logs.  At 1s and 5s, log how many
        // headings have rendered so far — gives the user
        // (and us) signal on whether ReactMarkdown is making
        // progress or wedged.
        const so_far = document.querySelectorAll(
          "h1, h2, h3, h4, h5, h6",
        ).length;
        console.log(
          `[ManualViewer] still polling at ${
            attempts * 200
          }ms; ${so_far} headings rendered so far`,
        );
      }
    }, 200);
    return () => window.clearInterval(interval);
  }, [manual?.content]);

  // Compute the base URL for rewriting relative image paths.
  // md_file_path is like "MWS-150-A/manual.md", so the base
  // directory is "MWS-150-A/".
  const imageBaseUrl = useMemo(() => {
    if (!manual?.md_file_path) return "/manuals/data/";
    const dir = manual.md_file_path.replace(/[^/]*$/, "");
    return `/manuals/data/${dir}`;
  }, [manual?.md_file_path]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin mr-2 text-muted-foreground" />
        <span className="text-sm text-muted-foreground">Loading manual...</span>
      </div>
    );
  }

  if (error || !manual) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4 mr-1" />
          {t("manuals.backToList")}
        </Button>
        <Alert variant="destructive">
          <AlertDescription>{error || "Manual not found"}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const body = manual.content
    ? cleanPageMarkers(stripFrontmatter(manual.content))
    : null;

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="h-4 w-4 mr-1" />
        {t("manuals.backToList")}
      </Button>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-muted-foreground" />
            <CardTitle className="text-lg">{manual.filename}</CardTitle>
          </div>
          {/* Metadata banner */}
          <div className="flex flex-wrap gap-2 mt-2">
            {manual.vehicle_model && (
              <Badge variant="outline">{manual.vehicle_model}</Badge>
            )}
            {manual.page_count && (
              <Badge variant="secondary">
                {manual.page_count} {t("manuals.pages")}
              </Badge>
            )}
            {manual.section_count && (
              <Badge variant="secondary">
                {manual.section_count} {t("manuals.sections")}
              </Badge>
            )}
            {manual.chunk_count && (
              <Badge variant="secondary">
                {manual.chunk_count} {t("manuals.chunks")}
              </Badge>
            )}
            {manual.language && (
              <Badge variant="secondary">{manual.language}</Badge>
            )}
            {manual.converter && (
              <Badge variant="outline">{manual.converter}</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {body ? (
            <article className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // Rewrite relative image paths to serve
                  // from the nginx /manuals/data/ endpoint.
                  img: ({ node, src, alt, ...props }) => {
                    const rawSrc = typeof src === "string" ? src : "";
                    let resolvedSrc = rawSrc;
                    if (
                      resolvedSrc &&
                      !resolvedSrc.startsWith("http") &&
                      !resolvedSrc.startsWith("/")
                    ) {
                      resolvedSrc = imageBaseUrl + resolvedSrc;
                    }
                    return (
                      <img
                        src={resolvedSrc}
                        alt={alt || ""}
                        loading="lazy"
                        className="max-w-full h-auto rounded"
                        {...props}
                      />
                    );
                  },
                  // Open external links in new tab.
                  a: ({ node, href, children, ...props }) => (
                    <a
                      href={href}
                      target={href?.startsWith("http") ? "_blank" : undefined}
                      rel={href?.startsWith("http") ? "noopener noreferrer" : undefined}
                      {...props}
                    >
                      {children}
                    </a>
                  ),
                  // Auto-id all headings using the same slugify
                  // logic as the backend so citations
                  // (`/manuals/<id>#<slug>`) deep-link reliably.
                  h1: ({ node, children, ...props }) => (
                    <h1 id={slugify(extractTextForSlug(children))} {...props}>
                      {children}
                    </h1>
                  ),
                  h2: ({ node, children, ...props }) => (
                    <h2 id={slugify(extractTextForSlug(children))} {...props}>
                      {children}
                    </h2>
                  ),
                  h3: ({ node, children, ...props }) => (
                    <h3 id={slugify(extractTextForSlug(children))} {...props}>
                      {children}
                    </h3>
                  ),
                  h4: ({ node, children, ...props }) => (
                    <h4 id={slugify(extractTextForSlug(children))} {...props}>
                      {children}
                    </h4>
                  ),
                  h5: ({ node, children, ...props }) => (
                    <h5 id={slugify(extractTextForSlug(children))} {...props}>
                      {children}
                    </h5>
                  ),
                  h6: ({ node, children, ...props }) => (
                    <h6 id={slugify(extractTextForSlug(children))} {...props}>
                      {children}
                    </h6>
                  ),
                }}
              >
                {body}
              </ReactMarkdown>
            </article>
          ) : (
            <p className="text-sm text-muted-foreground">
              No content available.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
