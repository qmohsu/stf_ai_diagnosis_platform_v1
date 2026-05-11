"use client";

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
        <ol className="space-y-2 text-sm">
          {entry.golden_citations.map((c, i) => {
            const href = `/manuals/${encodeURIComponent(c.manual_id)}#${encodeURIComponent(c.slug)}`;
            return (
              <li key={`${c.slug}-${i}`}>
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  className="block rounded border-l-4 border-primary/40 bg-muted/20 px-3 py-2 transition hover:border-primary hover:bg-muted/40"
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
              </li>
            );
          })}
        </ol>
      </section>
    </div>
  );
}
