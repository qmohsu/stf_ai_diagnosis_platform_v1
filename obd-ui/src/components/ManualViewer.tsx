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
  // Two-phase wait:
  //   1. Poll up to 20s for the target heading element to
  //      appear in the DOM.
  //   2. Once found, wait for `document.scrollHeight` to be
  //      stable for 4 consecutive ticks (800ms) before calling
  //      `scrollIntoView`.  This is the critical step: on a
  //      509K-char manual the target heading shows up early
  //      (~200ms) but ReactMarkdown is still rendering more
  //      content ABOVE it, which keeps shifting its final
  //      offsetTop downward.  If we scroll on first sight, the
  //      browser parks at a now-stale screen position; by the
  //      time render finishes, the heading has migrated far
  //      below the visible viewport.
  //
  // Total budget capped at 20s; if the heading never appears,
  // dump diagnostics so the failure mode is debuggable.
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
    const maxAttempts = 100; // 100 × 200ms = 20s
    let lastHeight = 0;
    let stableTicks = 0;
    const requiredStableTicks = 4; // 4 × 200ms = 800ms stable
    let logged_found_at: number | null = null;

    const interval = window.setInterval(() => {
      attempts += 1;
      const el = document.getElementById(targetId);
      const currentHeight =
        document.documentElement.scrollHeight;

      if (!el) {
        if (attempts >= maxAttempts) {
          window.clearInterval(interval);
          const allHeadings = Array.from(
            document.querySelectorAll(
              "h1, h2, h3, h4, h5, h6",
            ),
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
          console.warn(
            "[ManualViewer] First 10 heading ids found:",
            allIds.slice(0, 10),
          );
        } else if (attempts === 5 || attempts === 25) {
          const so_far = document.querySelectorAll(
            "h1, h2, h3, h4, h5, h6",
          ).length;
          console.log(
            `[ManualViewer] still polling at ${
              attempts * 200
            }ms; ${so_far} headings rendered so far`,
          );
        }
        return;
      }

      // Element exists — log once.
      if (logged_found_at === null) {
        logged_found_at = attempts;
        console.log(
          `[ManualViewer] target heading found at ${
            attempts * 200
          }ms; waiting for layout to stabilise before scrolling`,
        );
      }

      // Wait for document height to stabilise — ReactMarkdown
      // is likely still streaming content above the heading,
      // which would shift its final position.
      if (currentHeight === lastHeight) {
        stableTicks += 1;
      } else {
        stableTicks = 0;
        lastHeight = currentHeight;
      }

      if (stableTicks >= requiredStableTicks) {
        window.clearInterval(interval);
        const fromFound = attempts - (logged_found_at ?? attempts);
        console.log(
          `[ManualViewer] layout stable at ${
            attempts * 200
          }ms (waited ${
            fromFound * 200
          }ms after first sight); scrolling now.  scrollHeight=${currentHeight}px`,
        );
        // Use absolute-Y scroll with `behavior: "auto"` (which
        // most browsers interpret as instant) instead of
        // `scrollIntoView({ behavior: "smooth" })`.  Smooth
        // scroll for a 350K-pixel offset takes 1-3 seconds,
        // and if anything (React #418 re-hydration, image
        // load) shifts layout mid-animation the scroll stops
        // at an intermediate position.  Instant scroll is
        // unconditionally robust.
        //
        // Defend against subsequent layout shifts (re-renders,
        // image loads, font swaps) by re-scrolling at 300ms,
        // 1s, and 2.5s.  Cheap; the only way it can be wrong
        // is if the user has manually scrolled away — the
        // 4-second highlight gives them time to see where
        // they're meant to be before the last re-scroll.
        const scrollToEl = () => {
          const rect = el.getBoundingClientRect();
          const targetY = rect.top + window.scrollY;
          window.scrollTo({ top: targetY, behavior: "auto" });
          console.log(
            `[ManualViewer] scrolled; target Y=${targetY.toFixed(
              0,
            )}, current scrollY=${window.scrollY.toFixed(0)}`,
          );
        };
        scrollToEl();
        window.setTimeout(scrollToEl, 300);
        window.setTimeout(scrollToEl, 1000);
        window.setTimeout(scrollToEl, 2500);
        el.classList.add("ring-2", "ring-primary", "rounded");
        window.setTimeout(() => {
          el.classList.remove(
            "ring-2",
            "ring-primary",
            "rounded",
          );
        }, 4000);
        return;
      }

      if (attempts >= maxAttempts) {
        window.clearInterval(interval);
        // Layout never stabilised within 20s — scroll anyway
        // as a last resort, with a warning.
        console.warn(
          `[ManualViewer] layout never stabilised after ${
            attempts * 200
          }ms; scrolling as a last resort. scrollHeight is still moving (lastHeight=${lastHeight}, current=${currentHeight}).`,
        );
        el.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
        el.classList.add("ring-2", "ring-primary", "rounded");
        window.setTimeout(() => {
          el.classList.remove(
            "ring-2",
            "ring-primary",
            "rounded",
          );
        }, 2400);
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
