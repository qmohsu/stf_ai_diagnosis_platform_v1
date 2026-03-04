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
} from "lucide-react";
import { getDiagnosisHistory } from "@/lib/api";
import type { DiagnosisHistoryItem } from "@/lib/types";

const PAGE_SIZE = 5;

interface DiagnosisHistoryViewProps {
  sessionId: string;
  active?: boolean;
  provider?: "local" | "premium";
}

export function DiagnosisHistoryView({
  sessionId,
  active = true,
  provider,
}: DiagnosisHistoryViewProps) {
  const [items, setItems] = useState<
    DiagnosisHistoryItem[]
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
        const data = await getDiagnosisHistory(
          sessionId,
          PAGE_SIZE,
          pageNum * PAGE_SIZE,
          provider,
        );
        setItems(data.items);
        setTotal(data.total);
        setPage(pageNum);
        setLoaded(true);
      } catch (err: unknown) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load history",
        );
      } finally {
        setLoading(false);
      }
    },
    [sessionId, provider],
  );

  // Reset when provider changes so each sub-tab
  // loads its own data independently.
  useEffect(() => {
    setLoaded(false);
    setItems([]);
    setTotal(0);
    setPage(0);
    setExpanded(new Set());
  }, [provider]);

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
            <span>Loading diagnosis history...</span>
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
            Diagnosis History
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No {provider === "premium" ? "cloud" : provider === "local" ? "local" : ""} diagnosis
            generations yet. Generate a diagnosis from the
            &quot;AI Diagnostic Result&quot; tab first.
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
            Diagnosis History
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {total}{" "}
              {total === 1
                ? "generation"
                : "generations"}
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
          return (
            <div
              key={item.id}
              className={
                "border rounded-lg p-4 space-y-2"
                + (item.provider === "premium"
                  ? " border-l-4 border-l-primary"
                  : " border-l-4 border-l-muted-foreground/30")
              }
            >
              <div className="flex items-center gap-2 flex-wrap">
                <Badge
                  variant={
                    item.provider === "premium"
                      ? "default"
                      : "secondary"
                  }
                >
                  {item.provider === "premium"
                    ? "Cloud"
                    : "Local"}
                </Badge>
                <span className="text-sm font-mono text-muted-foreground">
                  {item.model_name}
                </span>
                <span className="text-xs text-muted-foreground ml-auto">
                  {formatTimestamp(item.created_at)}
                </span>
              </div>

              {isExpanded ? (
                <div className="space-y-2">
                  <pre className="whitespace-pre-wrap text-sm leading-relaxed font-sans bg-muted/50 rounded p-3 max-h-96 overflow-y-auto">
                    {item.diagnosis_text}
                  </pre>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => toggleExpand(item.id)}
                  >
                    Collapse
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground line-clamp-2">
                    {item.diagnosis_text}
                  </p>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => toggleExpand(item.id)}
                  >
                    Show full text
                  </Button>
                </div>
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
