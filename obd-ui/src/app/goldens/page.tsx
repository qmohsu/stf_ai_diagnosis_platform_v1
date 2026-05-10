"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle,
  CircleDot,
  Loader2,
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
  GoldenBucket,
  GoldenEntrySummary,
  GoldenReviewStatus,
} from "@/lib/types";

const BUCKETS: GoldenBucket[] = [
  "lookup",
  "procedural",
  "cross-section",
  "image-required",
  "adversarial",
];

function reviewBadge(status: GoldenReviewStatus | null) {
  if (status === null) {
    return (
      <Badge variant="outline" className="gap-1 text-muted-foreground">
        <CircleDot className="h-3 w-3" />
        Unreviewed / 未評
      </Badge>
    );
  }
  if (status === "draft") {
    return (
      <Badge variant="secondary" className="gap-1">
        <Loader2 className="h-3 w-3" />
        Draft / 草稿
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
        Accept / 採用
      </Badge>
    );
  }
  if (status === "needs_revision") {
    return (
      <Badge
        variant="default"
        className="gap-1 bg-amber-500 hover:bg-amber-600"
      >
        Revise / 需修訂
      </Badge>
    );
  }
  if (status === "reject") {
    return (
      <Badge variant="destructive" className="gap-1">
        <XCircle className="h-3 w-3" />
        Reject / 拒絕
      </Badge>
    );
  }
  return null;
}

export default function GoldensListingPage() {
  const [items, setItems] = useState<GoldenEntrySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bucketFilter, setBucketFilter] = useState<string>("all");

  useEffect(() => {
    setLoading(true);
    setError(null);
    listGoldens({
      bucket:
        bucketFilter === "all"
          ? undefined
          : (bucketFilter as GoldenBucket),
      limit: 200,
    })
      .then((res) => setItems(res.items))
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, [bucketFilter]);

  const grouped = useMemo(() => {
    const out: Record<GoldenBucket, GoldenEntrySummary[]> = {
      lookup: [],
      procedural: [],
      "cross-section": [],
      "image-required": [],
      adversarial: [],
    };
    for (const item of items) {
      out[item.question_type].push(item);
    }
    return out;
  }, [items]);

  const reviewedCount = items.filter(
    (i) =>
      i.my_review_status !== null &&
      i.my_review_status !== "draft",
  ).length;

  return (
    <div className="container mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center gap-2">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Home
        </Link>
      </div>

      <Card>
        <CardHeader className="space-y-2">
          <CardTitle className="flex items-center gap-2">
            Golden Q&amp;A review / 黃金問答審查
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Workshop-expert validation of the golden set used for
            evaluating the AI diagnostic assistant.  Click any entry
            to read the question card and grade it.
            <br />
            供工作坊專家驗證 AI 診斷助手評估用黃金問答集。
            點選任一項以閱讀問題卡片並評分。
          </p>
          <div className="flex flex-wrap items-center gap-3 pt-2">
            <span className="text-sm text-muted-foreground">
              Reviewed: <span className="font-semibold tabular-nums">{reviewedCount}</span>
              {" "}/ {items.length}
            </span>
            <div className="ml-auto flex items-center gap-2">
              <span className="text-sm text-muted-foreground">
                Bucket:
              </span>
              <Select
                value={bucketFilter}
                onChange={(e) => setBucketFilter(e.target.value)}
                className="w-[200px]"
              >
                <option value="all">All buckets</option>
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
              Loading entries...
            </div>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {!loading && !error && items.length === 0 && (
            <div className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">
              No golden entries found.  The startup sync may not have
              run yet — check the API logs.
              <br />
              尚未找到任何黃金問答。可能 startup sync 尚未執行——
              請檢查 API 記錄。
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
                      No entries authored in this bucket yet.
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {bucketItems.map((item) => (
                        <li key={item.id}>
                          <Link
                            href={`/goldens/${encodeURIComponent(item.id)}`}
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
                                      requires image
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
                                {reviewBadge(item.my_review_status)}
                                {item.my_review_star !== null && (
                                  <div className="flex items-center gap-1 text-xs">
                                    <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />
                                    <span className="tabular-nums">
                                      {item.my_review_star}/5
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
