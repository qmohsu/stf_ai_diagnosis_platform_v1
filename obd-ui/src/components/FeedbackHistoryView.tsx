"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Alert,
  AlertDescription,
} from "@/components/ui/alert";
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  Star,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { getFeedbackHistory } from "@/lib/api";
import type { FeedbackHistoryItem } from "@/lib/types";

const PAGE_SIZE = 5;

const TAB_LABELS: Record<string, string> = {
  summary: "Summary",
  detailed: "Detailed",
  rag: "RAG",
  ai_diagnosis: "AI Diagnosis",
  premium_diagnosis: "Premium AI",
};

interface FeedbackHistoryViewProps {
  sessionId: string;
  active?: boolean;
}

export function FeedbackHistoryView({
  sessionId,
  active = true,
}: FeedbackHistoryViewProps) {
  const [items, setItems] = useState<
    FeedbackHistoryItem[]
  >([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(
    new Set(),
  );

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const fetchPage = useCallback(
    async (pageNum: number) => {
      setLoading(true);
      setError(null);
      try {
        const data = await getFeedbackHistory(
          sessionId,
          PAGE_SIZE,
          pageNum * PAGE_SIZE,
        );
        setItems(data.items);
        setTotal(data.total);
        setPage(pageNum);
        setLoaded(true);
      } catch (err: unknown) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load feedback",
        );
      } finally {
        setLoading(false);
      }
    },
    [sessionId],
  );

  useEffect(() => {
    if (active && !loaded) {
      fetchPage(0);
    }
  }, [active, loaded, fetchPage]);

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const formatTimestamp = (iso: string): string => {
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleString();
    } catch {
      return iso;
    }
  };

  const handlePrev = () => {
    if (page > 0) fetchPage(page - 1);
  };

  const handleNext = () => {
    if (page < totalPages - 1) fetchPage(page + 1);
  };

  if (loading && !loaded) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="flex items-center gap-3 justify-center text-sm text-muted-foreground py-8">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span>Loading feedback history...</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="p-6 space-y-4">
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
          <Button
            variant="outline"
            className="w-full"
            onClick={() => fetchPage(page)}
          >
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (loaded && total === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">
            Feedback History
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No feedback submitted yet. Use the feedback
            forms on each analysis tab to submit ratings
            and comments.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">
            Feedback History
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {total}{" "}
              {total === 1
                ? "submission"
                : "submissions"}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => fetchPage(page)}
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                "Refresh"
              )}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {items.map((item) => {
          const isExpanded = expanded.has(item.id);
          const hasComments =
            item.comments != null &&
            item.comments.length > 0;
          return (
            <div
              key={item.id}
              className="border rounded-lg p-4 space-y-2 border-l-4 border-l-muted-foreground/30"
            >
              {/* Header row */}
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="outline">
                  {TAB_LABELS[item.tab_name] ??
                    item.tab_name}
                </Badge>

                {/* Star rating */}
                <div className="flex gap-0.5">
                  {[1, 2, 3, 4, 5].map((star) => (
                    <Star
                      key={star}
                      className={
                        "h-4 w-4"
                        + (item.rating >= star
                          ? " fill-amber-400 text-amber-400"
                          : " text-gray-300")
                      }
                    />
                  ))}
                </div>

                {/* Helpful indicator */}
                {item.is_helpful ? (
                  <span className="flex items-center gap-1 text-xs text-green-600">
                    <ThumbsUp className="h-3 w-3" />
                    Helpful
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-xs text-red-500">
                    <ThumbsDown className="h-3 w-3" />
                    Not helpful
                  </span>
                )}

                <span className="text-xs text-muted-foreground ml-auto">
                  {formatTimestamp(item.created_at)}
                </span>
              </div>

              {/* Comments */}
              {hasComments && (
                <>
                  {isExpanded ? (
                    <div className="space-y-2">
                      <pre className="whitespace-pre-wrap text-sm leading-relaxed font-sans bg-muted/50 rounded p-3 max-h-96 overflow-y-auto">
                        {item.comments}
                      </pre>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          toggleExpand(item.id)
                        }
                      >
                        Collapse
                      </Button>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <p className="text-sm text-muted-foreground line-clamp-2">
                        {item.comments}
                      </p>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          toggleExpand(item.id)
                        }
                      >
                        Show full text
                      </Button>
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handlePrev}
              disabled={page === 0 || loading}
            >
              <ChevronLeft className="h-4 w-4 mr-1" />
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">
              Page {page + 1} of {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={handleNext}
              disabled={
                page >= totalPages - 1 || loading
              }
            >
              Next
              <ChevronRight className="h-4 w-4 ml-1" />
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
