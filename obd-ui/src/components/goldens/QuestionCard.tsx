"use client";

import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { GoldenEntryDetail } from "@/lib/types";

interface QuestionCardProps {
  entry: GoldenEntryDetail;
  /** Which language to display.  Independent of UI chrome
   *  language (which is driven by the header language switcher
   *  via i18n).  This toggle only controls the Q+A content. */
  language: "en" | "zh";
}

/** Resolve a manual-relative image path (as it appears in the
 *  manual's markdown source, e.g.
 *  ``images/<uuid>/_page_84_Picture_28.jpeg``) to the absolute
 *  URL served by nginx via the ``/manuals/data/`` alias.
 *
 *  Mirrors the resolution logic in `<ManualViewer>` so embedded
 *  figures on the golden card load from the same place the
 *  manual viewer does.  Returns the raw src when md_file_path
 *  is null (e.g. manual deleted) — the <img> will 404 visibly
 *  rather than silently break. */
function resolveImageUrl(
  rawSrc: string,
  mdFilePath: string | null,
): string {
  if (!rawSrc) return rawSrc;
  // Absolute URLs and root-relative URLs pass through unchanged.
  if (/^(https?:)?\/\//i.test(rawSrc) || rawSrc.startsWith("/")) {
    return rawSrc;
  }
  const dir = mdFilePath
    ? mdFilePath.replace(/[^/]*$/, "")
    : "";
  return `/manuals/data/${dir}${rawSrc}`;
}

/**
 * Renders one golden Q+A as a "card" — question, proposed
 * answer, and the source quotes from the manual.  Switching
 * `language` flips question + answer text between English
 * and Chinese; manual quotes always stay in their original
 * (Chinese) form because they're verbatim from the source.
 *
 * If the requested language is `zh` but the entry hasn't been
 * bilingualised yet, falls back to the English text with a
 * "translation pending" hint so the reviewer isn't blocked.
 */
export function QuestionCard({ entry, language }: QuestionCardProps) {
  const { t } = useTranslation();
  const useZh = language === "zh";
  const question =
    useZh && entry.question_zh ? entry.question_zh : entry.question_en;
  const summary =
    useZh && entry.golden_summary_zh
      ? entry.golden_summary_zh
      : entry.golden_summary_en;
  const fellBackToEn =
    useZh && (!entry.question_zh || !entry.golden_summary_zh);

  const mdFilePath = entry.md_file_path ?? null;
  // Whether any citation carries embedded figures.  Used to
  // gate the "open in manual" footer cue (image-required
  // entries get the figures inline, so the footer link is just
  // a convenience).
  const anyFiguresEmbedded = useMemo(
    () =>
      entry.golden_citations.some(
        (c) => (c.figure_image_paths?.length ?? 0) > 0,
      ),
    [entry.golden_citations],
  );

  return (
    <div className="space-y-4">
      {/* Metadata row: bucket / category / difficulty */}
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{entry.question_type}</Badge>
        <Badge variant="outline">{entry.category}</Badge>
        <Badge
          variant="outline"
          className={cn(
            entry.difficulty === "hard" && "border-orange-500 text-orange-600",
            entry.difficulty === "medium" && "border-amber-500 text-amber-600",
            entry.difficulty === "easy" && "border-green-500 text-green-600",
          )}
        >
          {entry.difficulty}
        </Badge>
        {entry.requires_image && (
          <Badge variant="outline" className="border-blue-500 text-blue-600">
            {t("goldens.questionCard.requiresImage")}
          </Badge>
        )}
        <code className="text-xs text-muted-foreground ml-auto">
          {entry.id}
        </code>
      </div>

      {fellBackToEn && (
        <div className="text-xs text-amber-600">
          {t("goldens.questionCard.fellBackToEn")}
        </div>
      )}

      {/* Question */}
      <section className="space-y-1">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          {t("goldens.questionCard.question")}
        </h3>
        <p className="text-base leading-relaxed">{question}</p>
        {entry.obd_context && (
          <div className="mt-2 rounded border border-border bg-muted/40 p-2 text-sm">
            <span className="font-semibold">
              {t("goldens.questionCard.obdContext")}:
            </span>{" "}
            {entry.obd_context}
          </div>
        )}
      </section>

      {/* Proposed answer */}
      <section className="space-y-1">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          {t("goldens.questionCard.proposedAnswer")}
        </h3>
        <div className="rounded-md border border-border bg-muted/30 p-3 text-sm leading-relaxed whitespace-pre-wrap">
          {summary}
        </div>
      </section>

      {/* Source quotes — each citation links into the raw
          manual at the cited section.  Opens in a new tab so the
          reviewer doesn't lose their review-form state. */}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          {t("goldens.questionCard.sourceQuotes")}
        </h3>
        <ol className="space-y-3 text-sm">
          {entry.golden_citations.map((c, i) => {
            // Carry the quote alongside the slug in the URL so
            // <ManualViewer> can scroll to the quoted text inside
            // the section, not just the section heading.  Fixes
            // GitHub Issue #101 — citations whose quote lives mid-
            // section used to land at the section's first page.
            // The query-param syntax stays well-formed because
            // the URL spec puts `?query` before `#fragment`.
            const href =
              `/manuals/${encodeURIComponent(c.manual_id)}` +
              `?q=${encodeURIComponent(c.quote)}` +
              `#${encodeURIComponent(c.slug)}`;
            const figures = c.figure_image_paths ?? [];
            return (
              <li
                key={`${c.slug}-${i}`}
                className="rounded border-l-4 border-primary/40 bg-muted/20 overflow-hidden"
              >
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  className="block px-3 py-2 transition hover:border-primary hover:bg-muted/40"
                  title={t("goldens.questionCard.openInManual")}
                >
                  <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span>
                      {t("goldens.questionCard.section")}:{" "}
                      <code className="font-semibold">{c.slug}</code>
                    </span>
                    <ExternalLink className="h-3 w-3 shrink-0 opacity-60" />
                  </div>
                  <blockquote className="mt-1 italic">
                    &ldquo;{c.quote}&rdquo;
                  </blockquote>
                </a>

                {figures.length > 0 && (
                  // Figures are embedded directly so the
                  // technician sees the manual's diagram
                  // without having to follow the citation
                  // hyperlink — this is the fix for the
                  // Towngas feedback in issue #89 where the
                  // textual quote alone (with figure-local
                  // labels a/b/c/d) was meaningless without
                  // the diagram.
                  <div className="border-t border-border bg-background/60 px-3 py-3 space-y-2">
                    <div className="text-xs text-muted-foreground">
                      {t("goldens.questionCard.figure")}
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      {figures.map((src, j) => {
                        const resolved = resolveImageUrl(
                          src,
                          mdFilePath,
                        );
                        return (
                          <a
                            key={`${src}-${j}`}
                            href={resolved}
                            target="_blank"
                            rel="noreferrer"
                            className="block rounded border border-border bg-white hover:border-primary transition"
                            title={t(
                              "goldens.questionCard.openFigure",
                            )}
                          >
                            <img
                              src={resolved}
                              alt={`${c.slug} figure ${j + 1}`}
                              className="w-full h-auto rounded"
                              loading="lazy"
                            />
                          </a>
                        );
                      })}
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
        {anyFiguresEmbedded && (
          <p className="text-xs text-muted-foreground italic">
            {t("goldens.questionCard.figuresEmbeddedNote")}
          </p>
        )}
      </section>
    </div>
  );
}
