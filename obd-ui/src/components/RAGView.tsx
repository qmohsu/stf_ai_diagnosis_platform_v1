"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { retrieveRAG } from "@/lib/api";
import type { RetrievalResult } from "@/lib/types";

interface RAGViewProps {
  ragQuery: string;
}

export function RAGView({ ragQuery }: RAGViewProps) {
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
        setError(err instanceof Error ? err.message : "Failed to retrieve RAG results"),
      )
      .finally(() => setLoading(false));
  }, [ragQuery]);

  return (
    <div className="space-y-6">
      {/* RAG Query Card */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">RAG Query</CardTitle>
        </CardHeader>
        <CardContent>
          {ragQuery ? (
            <p className="text-sm whitespace-pre-wrap">{ragQuery}</p>
          ) : (
            <p className="text-sm text-muted-foreground">No RAG query generated for this session.</p>
          )}
        </CardContent>
      </Card>

      {/* Retrieval Results Card */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Retrieval Results</CardTitle>
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
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {!loading && !error && results.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {ragQuery ? "No retrieval results found." : "No RAG query available to retrieve results."}
            </p>
          )}

          {!loading &&
            results.map((result, idx) => (
              <Card key={idx} className="border">
                <CardContent className="p-4 space-y-2">
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <h4 className="font-medium text-sm">
                      {result.section_title || `Chunk ${result.chunk_index}`}
                    </h4>
                    <Badge variant="secondary" className="text-xs">
                      {(result.score * 100).toFixed(1)}% relevance
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
