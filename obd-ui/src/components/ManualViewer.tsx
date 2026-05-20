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
 * Fallback heading-element lookup for citations whose target
 * slug doesn't correspond to a real markdown heading.
 *
 * Background: marker-pdf occasionally renders a section title
 * as plain text (preceded by HTML page-anchor spans but with
 * no ``#`` heading marker), so ReactMarkdown emits it as a
 * ``<p>`` with no ``id`` attribute.  ``getElementById(slug)``
 * then returns null and the deep-link silently fails.
 *
 * The "title at the end of the element's text" heuristic
 * locks onto these without false-matching the TOC table or
 * passing mentions in body paragraphs:
 *
 *   - Section-title <p>: ``<span id="page-91-4"></span><span
 *     id="page-91-2"></span>液壓煞車系統空氣的釋放`` — when
 *     ReactMarkdown escapes the raw HTML to literal text, the
 *     <p>'s textContent is roughly
 *     ``<spanid="page-91-4"></span><spanid="page-91-2"></span>液壓煞車系統空氣的釋放``
 *     — the slug *ends* the string.
 *   - TOC table cell: skipped via the TABLE/UL/OL filter below.
 *   - Body paragraph that references the section in passing:
 *     ``參閱第 3-5 頁的 "汽門間隙的調整"。`` — the slug is
 *     in the middle, the text ends with `"。](#page-83-2)`,
 *     so endsWith() rejects it.
 *
 * Allow up to 8 trailing non-whitespace chars after the slug
 * to tolerate trailing punctuation (full-width period, etc.)
 * the markdown might add.
 *
 * @param slug The decoded URL hash (e.g. ``液壓煞車系統空氣的釋放``).
 */
function findHeadingFallback(slug: string): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const article = document.querySelector("article");
  if (!article) return null;
  const normSlug = slug.replace(/\s+/g, "");
  if (!normSlug) return null;
  for (const child of Array.from(article.children) as HTMLElement[]) {
    if (["TABLE", "UL", "OL"].includes(child.tagName)) continue;
    const text = (child.textContent ?? "").replace(/\s+/g, "");
    if (!text.includes(normSlug)) continue;
    const hit = text.lastIndexOf(normSlug);
    const trailing = text.length - (hit + normSlug.length);
    if (trailing <= 8) {
      return child;
    }
  }
  return null;
}

/**
 * Find the cited quote inside a section's body and return the
 * element that should be scrolled into view + highlighted.
 *
 * Walks every text node between ``headingEl`` and the next
 * heading sibling (the section body), normalising whitespace
 * away so PDF-extraction artefacts like ``"凸輪 軸鏈輪"`` (with
 * a stray space) still match the golden's verbatim
 * ``"凸輪軸鏈輪"``.  This matters for CJK manuals where marker-
 * pdf occasionally injects spaces within Chinese words.
 *
 * Returns the closest block-level ancestor of the matched text
 * node so the highlight has something visible to land on (text
 * nodes themselves can't carry a CSS class).  Returns null on
 * any miss — callers should fall back to scrolling the heading.
 *
 * @param headingEl Section heading element (the slug anchor).
 * @param quote Verbatim quote string from the golden citation.
 */
function findQuoteTarget(
  headingEl: HTMLElement,
  quote: string,
): HTMLElement | null {
  // Normalise: drop all whitespace so PDF-extracted CJK lines
  // with stray spaces still match the golden's clean quote.
  const normalisedQuote = quote.replace(/\s+/g, "");
  if (!normalisedQuote) return null;

  // Section body = next siblings of the heading up to (but not
  // including) the next heading element.  Markdown headings in
  // the manual are emitted as direct children of <article>, so
  // walking nextElementSibling is sufficient.
  const bodyRoots: Element[] = [];
  let cursor: Element | null = headingEl.nextElementSibling;
  while (cursor && !/^H[1-6]$/.test(cursor.tagName)) {
    bodyRoots.push(cursor);
    cursor = cursor.nextElementSibling;
  }
  if (bodyRoots.length === 0) return null;

  // Walk every text node inside the body, recording each one's
  // start offset in the *normalised* concatenated stream.  We
  // also keep the original (un-normalised) text so we can map a
  // normalised match position back to the original text node.
  type Entry = {
    node: Text;
    nodeText: string;
    normText: string;
    normStart: number;
  };
  const entries: Entry[] = [];
  let normPos = 0;
  for (const root of bodyRoots) {
    const walker = document.createTreeWalker(
      root,
      NodeFilter.SHOW_TEXT,
    );
    let n = walker.nextNode() as Text | null;
    while (n) {
      const norm = n.data.replace(/\s+/g, "");
      if (norm.length > 0) {
        entries.push({
          node: n,
          nodeText: n.data,
          normText: norm,
          normStart: normPos,
        });
        normPos += norm.length;
      }
      n = walker.nextNode() as Text | null;
    }
  }
  if (entries.length === 0) return null;

  // Search the concatenated normalised body for the quote.
  const fullNorm = entries.map((e) => e.normText).join("");
  const hit = fullNorm.indexOf(normalisedQuote);
  if (hit === -1) return null;

  // Locate the text node containing the start of the match.
  for (const e of entries) {
    const nodeEnd = e.normStart + e.normText.length;
    if (hit < nodeEnd) {
      // Highlight the nearest block-level ancestor — text nodes
      // can't carry a class and inline ancestors are usually
      // too narrow to give visual prominence.
      const parent = e.node.parentElement;
      if (!parent) return null;
      // Walk up until we hit something block-ish (p, li, td,
      // h*, blockquote, div).  Fall back to the immediate
      // parent if nothing matches.
      const blockTags = new Set([
        "P", "LI", "TD", "TH", "BLOCKQUOTE", "DIV",
        "H1", "H2", "H3", "H4", "H5", "H6",
      ]);
      let block: HTMLElement | null = parent;
      while (block && !blockTags.has(block.tagName)) {
        block = block.parentElement;
      }
      return block ?? parent;
    }
  }
  return null;
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
    // Optional ?q=<urlencoded-quote> carries the cited quote
    // alongside the slug, so we can scroll to the quoted text
    // inside the section rather than just the section heading.
    // GitHub Issue #101.  Missing q = legacy slug-only deep-link;
    // behaviour is unchanged.
    let quoteParam: string | null = null;
    try {
      quoteParam = new URLSearchParams(
        window.location.search,
      ).get("q");
    } catch {
      quoteParam = null;
    }
    console.log(
      `[ManualViewer] deep-link target requested: "${targetId}"` +
        (quoteParam
          ? ` (quote: "${quoteParam.slice(0, 40)}…")`
          : ""),
    );

    let attempts = 0;
    const maxAttempts = 100; // 100 × 200ms = 20s
    let lastHeight = 0;
    let stableTicks = 0;
    const requiredStableTicks = 4; // 4 × 200ms = 800ms stable
    let logged_found_at: number | null = null;

    let usedFallback = false;
    const interval = window.setInterval(() => {
      attempts += 1;
      // Primary lookup by id (works when marker-pdf preserved
      // the section as a markdown heading).
      let el = document.getElementById(targetId);
      // Fallback: if no element carries the slug as its id,
      // scan the article for a paragraph whose text content
      // IS the section title.  This rescues sections that
      // marker-pdf rendered as plain text rather than as a
      // proper heading (e.g. 液壓煞車系統空氣的釋放).
      if (!el) {
        const fallback = findHeadingFallback(targetId);
        if (fallback) {
          if (!usedFallback) {
            console.log(
              `[ManualViewer] target id "${targetId}" not found ` +
                "as a heading; using text-content fallback " +
                `(<${fallback.tagName.toLowerCase()}>)`,
            );
            usedFallback = true;
          }
          el = fallback;
        }
      }
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

        // If the citation carried a quote (?q=...), try to find
        // the quoted text inside the section body and target
        // THAT instead of the section heading.  Falls back to
        // the heading on any miss (empty body, OCR mismatch
        // beyond whitespace differences, etc.) so legacy
        // slug-only links keep working unchanged.
        let target: HTMLElement = el;
        if (quoteParam) {
          const quoteTarget = findQuoteTarget(el, quoteParam);
          if (quoteTarget) {
            target = quoteTarget;
            console.log(
              "[ManualViewer] quote located inside section; " +
                "scrolling to quoted block instead of heading",
            );
          } else {
            console.warn(
              `[ManualViewer] quote not found in section "${targetId}"; ` +
                "falling back to section heading",
            );
          }
        }

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
        const scrollToTarget = () => {
          const rect = target.getBoundingClientRect();
          const targetY = rect.top + window.scrollY;
          window.scrollTo({ top: targetY, behavior: "auto" });
          console.log(
            `[ManualViewer] scrolled; target Y=${targetY.toFixed(
              0,
            )}, current scrollY=${window.scrollY.toFixed(0)}`,
          );
        };
        scrollToTarget();
        window.setTimeout(scrollToTarget, 300);
        window.setTimeout(scrollToTarget, 1000);
        window.setTimeout(scrollToTarget, 2500);
        target.classList.add(
          "ring-2",
          "ring-primary",
          "rounded",
        );
        window.setTimeout(() => {
          target.classList.remove(
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
