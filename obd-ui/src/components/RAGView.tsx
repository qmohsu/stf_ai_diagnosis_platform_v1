"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { retrieveRAG } from "@/lib/api";
import type { RetrievalResult } from "@/lib/types";

interface RAGViewProps {
  ragQuery: string;
}

export function RAGView({ ragQuery }: RAGViewProps) {
  const { t } = useTranslation();
  const [results, setResults] = useState<RetrievalResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ragQuery) return;
    setLoading(true);
    setError(null);
    retrieveRAG(ragQuery, 5)
      .then((data) => setResults(data.results))
      .catch((err) =>
        setError(err instanceof Error ? err.message : "RAG_RETRIEVE_FAILED"),
      )
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ragQuery]);

  return (
    <div className="space-y-6">
      {/* RAG Query Card */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{t("rag.query")}</CardTitle>
        </CardHeader>
        <CardContent>
          {ragQuery ? (
            <p className="text-sm whitespace-pre-wrap">{ragQuery}</p>
          ) : (
            <p className="text-sm text-muted-foreground">{t("rag.noQuery")}</p>
          )}
        </CardContent>
      </Card>

      {/* Retrieval Results Card */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{t("rag.results")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading && (
            <div className="space-y-3 animate-pulse">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-24 rounded-lg bg-muted" />
              ))}
            </div>
          )}

          {error && (
            <Alert variant="destructive">
              <AlertDescription>
                {error === "RAG_RETRIEVE_FAILED" ? t("rag.retrieveFailed") : error}
              </AlertDescription>
            </Alert>
          )}

          {!loading && !error && results.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {ragQuery ? t("rag.noResults") : t("rag.noQueryAvailable")}
            </p>
          )}

          {!loading &&
            results.map((result, idx) => (
              <Card key={idx} className="border">
                <CardContent className="p-4 space-y-2">
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <h4 className="font-medium text-sm">
                      {result.section_title || t("rag.chunk", { index: result.chunk_index })}
                    </h4>
                    <Badge variant="secondary" className="text-xs">
                      {t("rag.relevance", { score: (result.score * 100).toFixed(1) })}
                    </Badge>
                  </div>
                  <div className="flex gap-2 flex-wrap">
                    <Badge variant="outline" className="text-xs">
                      {result.source_type}
                    </Badge>
                    <Badge variant="outline" className="text-xs font-mono">
                      {result.doc_id}
                    </Badge>
                  </div>
                  <p className="text-sm text-muted-foreground whitespace-pre-wrap">
                    {result.text}
                  </p>
                </CardContent>
              </Card>
            ))}
        </CardContent>
      </Card>
    </div>
  );
}
