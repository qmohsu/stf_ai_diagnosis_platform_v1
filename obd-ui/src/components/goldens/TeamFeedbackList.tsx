"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Loader2,
  Star,
  Trash2,
  User as UserIcon,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useAuth } from "@/components/AuthProvider";
import {
  deleteReview,
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

function statusBadge(status: GoldenReviewStatus) {
  switch (status) {
    case "accept":
      return (
        <Badge className="gap-1 bg-green-600 hover:bg-green-700">
          <CheckCircle className="h-3 w-3" />
          Accept / 採用
        </Badge>
      );
    case "needs_revision":
      return (
        <Badge className="gap-1 bg-amber-500 hover:bg-amber-600">
          Revise / 需修訂
        </Badge>
      );
    case "reject":
      return (
        <Badge variant="destructive" className="gap-1">
          <XCircle className="h-3 w-3" />
          Reject / 拒絕
        </Badge>
      );
    case "draft":
    default:
      return (
        <Badge variant="secondary" className="gap-1">
          Draft / 草稿
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
        Audio unavailable: {error}
      </div>
    );
  }
  if (!url) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" /> Loading audio…
      </div>
    );
  }
  return <audio src={url} controls className="w-full max-w-md" />;
}

/** One feedback card.  Snapshot Q+A is collapsed by default. */
function FeedbackCard({
  review,
  isMine,
  onDeleted,
}: {
  review: TeamReviewItem;
  isMine: boolean;
  onDeleted: () => void;
}) {
  const [showSnapshot, setShowSnapshot] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
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

  async function handleDelete() {
    // Use the browser's native confirm dialog — simple,
    // no new dependency, and screen-reader accessible for free.
    // Bilingual prompt to match the rest of the page's framing.
    const ok = window.confirm(
      "Delete this review?  This cannot be undone.\n刪除此評分？此操作無法復原。",
    );
    if (!ok) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteReview(review.review_id);
      onDeleted();
    } catch (err) {
      setDeleteError(
        err instanceof Error ? err.message : String(err),
      );
      setDeleting(false);
    }
  }

  return (
    <article className="rounded-md border border-border bg-card p-4 space-y-3">
      {/* Header: reviewer + timestamp + status (+ delete if owner) */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1 text-sm font-semibold">
          <UserIcon className="h-4 w-4 text-muted-foreground" />
          {review.reviewer_username}
          {isMine && (
            <Badge
              variant="outline"
              className="ml-1 text-[10px] uppercase tracking-wide"
            >
              you / 您
            </Badge>
          )}
        </div>
        <span className="text-xs text-muted-foreground">
          {dateLabel}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {statusBadge(review.status)}
          {isMine && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleDelete}
              disabled={deleting}
              className="h-7 gap-1 px-2 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive"
              title="Delete this review / 刪除此評分"
            >
              {deleting ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Trash2 className="h-3 w-3" />
              )}
              Delete / 刪除
            </Button>
          )}
        </div>
      </div>

      {deleteError && (
        <Alert variant="destructive">
          <AlertDescription>{deleteError}</AlertDescription>
        </Alert>
      )}

      {/* Star ratings (overall + 3 dimensions) */}
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <div className="text-xs text-muted-foreground">
            Overall / 整體
          </div>
          <StarStatic value={review.star_rating} size={18} />
        </div>
        <div>
          <div className="text-xs text-muted-foreground">
            Question realism / 問題擬真度
          </div>
          <StarStatic value={review.question_realism_score} />
        </div>
        <div>
          <div className="text-xs text-muted-foreground">
            Answer correctness / 答案正確性
          </div>
          <StarStatic value={review.answer_correctness_score} />
        </div>
        <div>
          <div className="text-xs text-muted-foreground">
            Citation faithfulness / 引用的忠實度
          </div>
          <StarStatic value={review.citation_faithfulness_score} />
        </div>
      </div>

      {/* Notes */}
      {review.notes && (
        <div className="space-y-1">
          <div className="text-xs text-muted-foreground">
            Notes / 備註
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
            Audio feedback / 語音回饋
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
            Snapshot of Q+A at review time / 評分當時的問答快照
          </Button>
          {showSnapshot && (
            <div className="space-y-2 rounded border border-dashed border-border bg-muted/20 p-3 text-sm">
              <div>
                <div className="text-xs font-semibold uppercase text-muted-foreground">
                  Question
                </div>
                <p className="mt-1 whitespace-pre-wrap">
                  {review.snapshot_question_zh ??
                    review.snapshot_question_en ??
                    "(no snapshot)"}
                </p>
              </div>
              <div>
                <div className="text-xs font-semibold uppercase text-muted-foreground">
                  Proposed answer
                </div>
                <p className="mt-1 whitespace-pre-wrap">
                  {review.snapshot_summary_zh ??
                    review.snapshot_summary_en ??
                    "(no snapshot)"}
                </p>
              </div>
              {review.snapshot_citations &&
                review.snapshot_citations.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold uppercase text-muted-foreground">
                      Cited sources
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
  const { username: currentUsername } = useAuth();
  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<TeamReviewItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped after a card-level delete so we refetch the list.
  // Combined with the parent's refreshKey so either trigger
  // works.
  const [localBump, setLocalBump] = useState(0);

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
  }, [entryId, refreshKey, localBump]);

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
          History / 歷史評分
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
              No reviews submitted yet for this entry.
              <br />
              此項目尚無評分。
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
                onDeleted={() => setLocalBump((b) => b + 1)}
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
