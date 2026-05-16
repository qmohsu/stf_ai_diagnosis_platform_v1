"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Loader2,
  Star,
  User as UserIcon,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useAuth } from "@/components/AuthProvider";
import {
  fetchAnyReviewAudioBlob,
  listTeamReviews,
} from "@/lib/api";
import type {
  GoldenReviewStatus,
  TeamReviewItem,
} from "@/lib/types";

interface TeamFeedbackListProps {
  entryId: string;
  /** Bump to refresh after the user submits their own review. */
  refreshKey: number;
}

function StatusBadge({ status }: { status: GoldenReviewStatus }) {
  const { t } = useTranslation();
  switch (status) {
    case "accept":
      return (
        <Badge className="gap-1 bg-green-600 hover:bg-green-700">
          <CheckCircle className="h-3 w-3" />
          {t("goldens.listing.reviewStatus.accept")}
        </Badge>
      );
    case "needs_revision":
      return (
        <Badge className="gap-1 bg-amber-500 hover:bg-amber-600">
          {t("goldens.listing.reviewStatus.needsRevision")}
        </Badge>
      );
    case "reject":
      return (
        <Badge variant="destructive" className="gap-1">
          <XCircle className="h-3 w-3" />
          {t("goldens.listing.reviewStatus.reject")}
        </Badge>
      );
    case "draft":
    default:
      return (
        <Badge variant="secondary" className="gap-1">
          {t("goldens.listing.reviewStatus.draft")}
        </Badge>
      );
  }
}

/** Static 5-star display used in feedback cards. */
function StarStatic({
  value,
  size = 16,
}: {
  value: number | null;
  size?: number;
}) {
  return (
    <span className="inline-flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((n) => (
        <Star
          key={n}
          style={{ width: size, height: size }}
          className={
            value !== null && n <= value
              ? "fill-yellow-400 text-yellow-400"
              : "text-muted-foreground/40"
          }
        />
      ))}
      <span className="ml-1 text-xs tabular-nums text-muted-foreground">
        {value !== null ? `${value}/5` : "—"}
      </span>
    </span>
  );
}

/** Inline audio playback that fetches the (auth-gated) blob. */
function AudioPlayback({ reviewId }: { reviewId: string }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    fetchAnyReviewAudioBlob(reviewId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [reviewId]);

  if (error) {
    return (
      <div className="text-xs text-destructive">
        {t("goldens.teamFeedback.audioUnavailable")}: {error}
      </div>
    );
  }
  if (!url) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />{" "}
        {t("goldens.teamFeedback.loadingAudio")}
      </div>
    );
  }
  return <audio src={url} controls className="w-full max-w-md" />;
}

/** One feedback card.  Snapshot Q+A is collapsed by default.
 *
 *  Reviews are append-only and immutable: there is intentionally
 *  no delete affordance.  If a reviewer needs to revise an
 *  earlier grade, they post a NEW review — the most-recent submit
 *  is the team's headline status, and the older row stays as
 *  audit history. */
function FeedbackCard({
  review,
  isMine,
}: {
  review: TeamReviewItem;
  isMine: boolean;
}) {
  const { t } = useTranslation();
  const [showSnapshot, setShowSnapshot] = useState(false);
  const hasSnapshot =
    review.snapshot_question_en !== null ||
    review.snapshot_summary_en !== null;
  const dateLabel = useMemo(() => {
    try {
      return new Date(review.updated_at).toLocaleString();
    } catch {
      return review.updated_at;
    }
  }, [review.updated_at]);

  return (
    <article className="rounded-md border border-border bg-card p-4 space-y-3">
      {/* Header: reviewer + timestamp + status. */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1 text-sm font-semibold">
          <UserIcon className="h-4 w-4 text-muted-foreground" />
          {review.reviewer_username}
          {isMine && (
            <Badge
              variant="outline"
              className="ml-1 text-[10px] uppercase tracking-wide"
            >
              {t("goldens.teamFeedback.you")}
            </Badge>
          )}
        </div>
        <span className="text-xs text-muted-foreground">
          {dateLabel}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <StatusBadge status={review.status} />
        </div>
      </div>

      {/* Star ratings (overall + 3 dimensions) */}
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <div className="text-xs text-muted-foreground">
            {t("goldens.teamFeedback.overall")}
          </div>
          <StarStatic value={review.star_rating} size={18} />
        </div>
        <div>
          <div className="text-xs text-muted-foreground">
            {t("goldens.review.questionRealism")}
          </div>
          <StarStatic value={review.question_realism_score} />
        </div>
        <div>
          <div className="text-xs text-muted-foreground">
            {t("goldens.review.answerCorrectness")}
          </div>
          <StarStatic value={review.answer_correctness_score} />
        </div>
        <div>
          <div className="text-xs text-muted-foreground">
            {t("goldens.review.citationFaithfulness")}
          </div>
          <StarStatic value={review.citation_faithfulness_score} />
        </div>
      </div>

      {/* Notes */}
      {review.notes && (
        <div className="space-y-1">
          <div className="text-xs text-muted-foreground">
            {t("goldens.review.notes")}
          </div>
          <div className="whitespace-pre-wrap rounded border border-border bg-muted/30 px-3 py-2 text-sm">
            {review.notes}
          </div>
        </div>
      )}

      {/* Audio */}
      {review.has_audio && (
        <div className="space-y-1">
          <div className="text-xs text-muted-foreground">
            {t("goldens.teamFeedback.audio")}
          </div>
          <AudioPlayback reviewId={review.review_id} />
        </div>
      )}

      {/* Collapsible snapshot */}
      {hasSnapshot && (
        <div className="space-y-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-ml-2 h-7 gap-1 px-2 text-xs"
            onClick={() => setShowSnapshot((v) => !v)}
          >
            {showSnapshot ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            {t("goldens.teamFeedback.snapshot")}
          </Button>
          {showSnapshot && (
            <div className="space-y-2 rounded border border-dashed border-border bg-muted/20 p-3 text-sm">
              <div>
                <div className="text-xs font-semibold uppercase text-muted-foreground">
                  {t("goldens.teamFeedback.snapshotQuestion")}
                </div>
                <p className="mt-1 whitespace-pre-wrap">
                  {review.snapshot_question_zh ??
                    review.snapshot_question_en ??
                    t("goldens.teamFeedback.noSnapshot")}
                </p>
              </div>
              <div>
                <div className="text-xs font-semibold uppercase text-muted-foreground">
                  {t("goldens.teamFeedback.snapshotAnswer")}
                </div>
                <p className="mt-1 whitespace-pre-wrap">
                  {review.snapshot_summary_zh ??
                    review.snapshot_summary_en ??
                    t("goldens.teamFeedback.noSnapshot")}
                </p>
              </div>
              {review.snapshot_citations &&
                review.snapshot_citations.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold uppercase text-muted-foreground">
                      {t("goldens.teamFeedback.snapshotCitedSources")}
                    </div>
                    <ul className="mt-1 space-y-1">
                      {review.snapshot_citations.map((c, i) => (
                        <li
                          key={`${c.slug}-${i}`}
                          className="text-xs"
                        >
                          <code className="text-muted-foreground">
                            {c.slug}
                          </code>{" "}
                          — &ldquo;{c.quote}&rdquo;
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
            </div>
          )}
        </div>
      )}
    </article>
  );
}

/**
 * Team feedback panel: expandable list of every team member's
 * review on this entry.  Loaded lazily on first expand so the
 * detail page isn't penalised when no one cares about history.
 */
export function TeamFeedbackList({
  entryId,
  refreshKey,
}: TeamFeedbackListProps) {
  const { t } = useTranslation();
  const { username: currentUsername } = useAuth();
  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<TeamReviewItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Always fetch the count up-front so the toggle label can
  // show "Show history (N)" even before the user expands.
  useEffect(() => {
    let cancelled = false;
    listTeamReviews(entryId)
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [entryId, refreshKey]);

  const handleToggle = () => {
    setExpanded((v) => !v);
    if (!expanded && items.length === 0 && total === null) {
      // Defensive: refetch if we somehow opened with no data.
      setLoading(true);
      listTeamReviews(entryId)
        .then((res) => {
          setItems(res.items);
          setTotal(res.total);
        })
        .catch((err) =>
          setError(err instanceof Error ? err.message : String(err)),
        )
        .finally(() => setLoading(false));
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <Button
          type="button"
          variant="outline"
          onClick={handleToggle}
          className="gap-2"
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
          {t("goldens.teamFeedback.historyButton")}
          <Badge variant="secondary" className="ml-1 tabular-nums">
            {total ?? "—"}
          </Badge>
        </Button>
        {loading && (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        )}
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {expanded && (
        <>
          {items.length === 0 && !loading && !error && (
            <p className="text-sm italic text-muted-foreground">
              {t("goldens.teamFeedback.noReviewsYet")}
            </p>
          )}
          <div className="space-y-3">
            {items.map((r) => (
              <FeedbackCard
                key={r.review_id}
                review={r}
                isMine={
                  !!currentUsername &&
                  r.reviewer_username === currentUsername
                }
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
