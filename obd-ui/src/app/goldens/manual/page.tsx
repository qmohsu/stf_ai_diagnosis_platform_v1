"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  CheckCircle,
  CircleDot,
  Loader2,
  MessageSquare,
  Star,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { listGoldens } from "@/lib/api";
import type {
  ManualGoldenBucket,
  GoldenEntrySummary,
  GoldenReviewStatus,
} from "@/lib/types";

const BUCKETS: ManualGoldenBucket[] = [
  "lookup",
  "procedural",
  "cross-section",
  "image-required",
  "adversarial",
];

function ReviewBadge({
  status,
}: {
  status: GoldenReviewStatus | null;
}) {
  const { t } = useTranslation();
  if (status === null) {
    return (
      <Badge variant="outline" className="gap-1 text-muted-foreground">
        <CircleDot className="h-3 w-3" />
        {t("goldens.listing.reviewStatus.unreviewed")}
      </Badge>
    );
  }
  if (status === "draft") {
    return (
      <Badge variant="secondary" className="gap-1">
        <Loader2 className="h-3 w-3" />
        {t("goldens.listing.reviewStatus.draft")}
      </Badge>
    );
  }
  if (status === "accept") {
    return (
      <Badge
        variant="default"
        className="gap-1 bg-green-600 hover:bg-green-700"
      >
        <CheckCircle className="h-3 w-3" />
        {t("goldens.listing.reviewStatus.accept")}
      </Badge>
    );
  }
  if (status === "needs_revision") {
    return (
      <Badge
        variant="default"
        className="gap-1 bg-amber-500 hover:bg-amber-600"
      >
        {t("goldens.listing.reviewStatus.needsRevision")}
      </Badge>
    );
  }
  if (status === "reject") {
    return (
      <Badge variant="destructive" className="gap-1">
        <XCircle className="h-3 w-3" />
        {t("goldens.listing.reviewStatus.reject")}
      </Badge>
    );
  }
  return null;
}

export default function GoldensListingPage() {
  const { t } = useTranslation();
  const [items, setItems] = useState<GoldenEntrySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bucketFilter, setBucketFilter] = useState<string>("all");

  useEffect(() => {
    setLoading(true);
    setError(null);
    listGoldens({
      lane: "manual",
      bucket:
        bucketFilter === "all"
          ? undefined
          : (bucketFilter as ManualGoldenBucket),
      limit: 200,
    })
      .then((res) => setItems(res.items))
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, [bucketFilter]);

  const grouped = useMemo(() => {
    const out: Record<ManualGoldenBucket, GoldenEntrySummary[]> = {
      lookup: [],
      procedural: [],
      "cross-section": [],
      "image-required": [],
      adversarial: [],
    };
    for (const item of items) {
      // Cast is safe because listGoldens({lane: "manual"}) only
      // returns manual-lane entries.  TypeScript can't infer
      // that from the API call so we narrow explicitly.
      const bucket = item.question_type as ManualGoldenBucket;
      if (bucket in out) {
        out[bucket].push(item);
      }
    }
    return out;
  }, [items]);

  const reviewedCount = items.filter(
    (i) =>
      i.latest_review_status !== null &&
      i.latest_review_status !== "draft",
  ).length;

  return (
    <div className="container mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center gap-2">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          {t("goldens.common.home")}
        </Link>
      </div>

      <Card>
        <CardHeader className="space-y-2">
          <CardTitle className="flex items-center gap-2">
            {t("goldens.listing.title")}
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            {t("goldens.listing.description")}
          </p>
          <div className="flex flex-wrap items-center gap-3 pt-2">
            <span className="text-sm text-muted-foreground">
              {t("goldens.listing.reviewed")}:{" "}
              <span className="font-semibold tabular-nums">
                {reviewedCount}
              </span>{" "}
              / {items.length}
            </span>
            <div className="ml-auto flex items-center gap-2">
              <span className="text-sm text-muted-foreground">
                {t("goldens.listing.bucket")}:
              </span>
              <Select
                value={bucketFilter}
                onChange={(e) => setBucketFilter(e.target.value)}
                className="w-[200px]"
              >
                <option value="all">
                  {t("goldens.listing.allBuckets")}
                </option>
                {BUCKETS.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {loading && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("goldens.listing.loadingEntries")}
            </div>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {!loading && !error && items.length === 0 && (
            <div className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">
              {t("goldens.listing.noEntries")}
            </div>
          )}

          {!loading &&
            !error &&
            BUCKETS.map((bucket) => {
              const bucketItems = grouped[bucket];
              if (
                bucketItems.length === 0 &&
                bucketFilter !== "all" &&
                bucketFilter !== bucket
              ) {
                return null;
              }
              return (
                <section key={bucket} className="space-y-2">
                  <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                    {bucket}{" "}
                    <span className="ml-1 text-xs font-normal text-muted-foreground/70">
                      ({bucketItems.length})
                    </span>
                  </h3>
                  {bucketItems.length === 0 ? (
                    <p className="text-sm italic text-muted-foreground">
                      {t("goldens.listing.noEntriesInBucket")}
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {bucketItems.map((item) => (
                        <li key={item.id}>
                          <Link
                            href={`/goldens/manual/${encodeURIComponent(item.id)}`}
                            className="block rounded border border-border bg-card p-3 transition hover:border-primary hover:bg-muted/40"
                          >
                            <div className="flex items-start gap-3">
                              <div className="flex-1 space-y-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge variant="outline">
                                    {item.difficulty}
                                  </Badge>
                                  {item.has_zh && (
                                    <Badge
                                      variant="secondary"
                                      className="text-xs"
                                    >
                                      EN+中
                                    </Badge>
                                  )}
                                  {item.requires_image && (
                                    <Badge
                                      variant="outline"
                                      className="border-blue-500 text-xs text-blue-600"
                                    >
                                      {t("goldens.questionCard.requiresImage")}
                                    </Badge>
                                  )}
                                </div>
                                <p className="line-clamp-2 text-sm">
                                  {item.question_zh ?? item.question_en}
                                </p>
                                <code className="text-xs text-muted-foreground">
                                  {item.id}
                                </code>
                              </div>
                              <div className="flex flex-col items-end gap-1">
                                <ReviewBadge
                                  status={item.latest_review_status}
                                />
                                {item.latest_review_star !== null && (
                                  <div className="flex items-center gap-1 text-xs">
                                    <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />
                                    <span className="tabular-nums">
                                      {item.latest_review_star}/5
                                    </span>
                                  </div>
                                )}
                                {item.latest_reviewer_username && (
                                  <div className="text-[10px] text-muted-foreground">
                                    {t(
                                      "goldens.listing.byReviewer",
                                      {
                                        user: item.latest_reviewer_username,
                                      },
                                    )}
                                  </div>
                                )}
                                {item.review_count > 0 && (
                                  <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                                    <MessageSquare className="h-3 w-3" />
                                    <span className="tabular-nums">
                                      {item.review_count}
                                    </span>
                                  </div>
                                )}
                              </div>
                            </div>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              );
            })}
        </CardContent>
      </Card>
    </div>
  );
}
