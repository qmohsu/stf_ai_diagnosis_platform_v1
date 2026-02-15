"use client";

import { useState } from "react";
import type { LogSummaryV2, ParsedSummary } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SummaryView } from "@/components/SummaryView";
import { DetailedView } from "@/components/DetailedView";
import { RAGView } from "@/components/RAGView";
import { AIDiagnosisView } from "@/components/AIDiagnosisView";
import { FeedbackForm } from "@/components/FeedbackForm";
import { formatDuration } from "@/lib/utils";

interface AnalysisLayoutProps {
  sessionId: string;
  data: LogSummaryV2;
  parsedSummary: ParsedSummary | null;
  diagnosisText: string | null;
}

export function AnalysisLayout({ sessionId, data, parsedSummary, diagnosisText: initialDiagnosisText }: AnalysisLayoutProps) {
  const [diagnosisText, setDiagnosisText] = useState<string | null>(initialDiagnosisText);
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
          <TabsTrigger value="ai_diagnosis">AI Diagnostic Result</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" forceMount className="space-y-6 data-[state=inactive]:hidden">
          <SummaryView data={data} />
          <FeedbackForm sessionId={sessionId} feedbackTab="summary" />
        </TabsContent>

        <TabsContent value="detailed" forceMount className="space-y-6 data-[state=inactive]:hidden">
          <DetailedView data={data} />
          <FeedbackForm sessionId={sessionId} feedbackTab="detailed" />
        </TabsContent>

        <TabsContent value="rag" forceMount className="space-y-6 data-[state=inactive]:hidden">
          <RAGView ragQuery={parsedSummary?.rag_query ?? ""} />
          <FeedbackForm sessionId={sessionId} feedbackTab="rag" />
        </TabsContent>

        <TabsContent value="ai_diagnosis" forceMount className="space-y-6 data-[state=inactive]:hidden">
          <AIDiagnosisView sessionId={sessionId} initialDiagnosisText={diagnosisText} onDiagnosisGenerated={setDiagnosisText} />
          <FeedbackForm sessionId={sessionId} feedbackTab="ai_diagnosis" />
        </TabsContent>
      </Tabs>
    </div>
  );
}
