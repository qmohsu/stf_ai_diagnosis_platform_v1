"use client";

import type { LogSummaryV2, ParsedSummary } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SummaryView } from "@/components/SummaryView";
import { DetailedView } from "@/components/DetailedView";
import { RAGView } from "@/components/RAGView";
import { FeedbackForm } from "@/components/FeedbackForm";
import { formatDuration } from "@/lib/utils";

interface AnalysisLayoutProps {
  sessionId: string;
  data: LogSummaryV2;
  parsedSummary: ParsedSummary | null;
}

export function AnalysisLayout({ sessionId, data, parsedSummary }: AnalysisLayoutProps) {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-2xl font-bold">Analysis Results</h2>
          <Badge variant="secondary" className="font-mono">
            {data.vehicle_id}
          </Badge>
        </div>
        <div className="flex items-center gap-4 text-sm text-muted-foreground flex-wrap">
          <span>
            {data.time_range.start} — {data.time_range.end}
          </span>
          <span>Duration: {formatDuration(data.time_range.duration_seconds)}</span>
          <span>{data.time_range.sample_count} samples</span>
          {data.dtc_codes.length > 0 && (
            <div className="flex gap-1">
              {data.dtc_codes.map((code) => (
                <Badge key={code} variant="destructive" className="text-xs">
                  {code}
                </Badge>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="summary">
        <TabsList>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="detailed">Detailed</TabsTrigger>
          <TabsTrigger value="rag">RAG</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-6">
          <SummaryView data={data} />
          <FeedbackForm sessionId={sessionId} feedbackTab="summary" />
        </TabsContent>

        <TabsContent value="detailed" className="space-y-6">
          <DetailedView data={data} />
          <FeedbackForm sessionId={sessionId} feedbackTab="detailed" />
        </TabsContent>

        <TabsContent value="rag" className="space-y-6">
          <RAGView ragQuery={parsedSummary?.rag_query ?? ""} />
          <FeedbackForm sessionId={sessionId} feedbackTab="rag" />
        </TabsContent>
      </Tabs>
    </div>
  );
}
