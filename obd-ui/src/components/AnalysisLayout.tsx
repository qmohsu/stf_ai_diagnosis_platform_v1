"use client";

import { useEffect, useState } from "react";
import type { LogSummaryV2, ParsedSummary } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SummaryView } from "@/components/SummaryView";
import { DetailedView } from "@/components/DetailedView";
import { RAGView } from "@/components/RAGView";
import { AIDiagnosisView } from "@/components/AIDiagnosisView";
import { DiagnosisHistoryView } from "@/components/DiagnosisHistoryView";
import { FeedbackHistoryView } from "@/components/FeedbackHistoryView";
import { FeedbackForm } from "@/components/FeedbackForm";
import { formatDuration } from "@/lib/utils";
import { getPremiumModels } from "@/lib/api";

interface AnalysisLayoutProps {
  sessionId: string;
  data: LogSummaryV2;
  parsedSummary: ParsedSummary | null;
  diagnosisText: string | null;
  premiumDiagnosisText: string | null;
  premiumLlmEnabled: boolean;
}

export function AnalysisLayout({
  sessionId,
  data,
  parsedSummary,
  diagnosisText: initialDiagnosisText,
  premiumDiagnosisText: initialPremiumDiagnosisText,
  premiumLlmEnabled,
}: AnalysisLayoutProps) {
  const [diagnosisText, setDiagnosisText] = useState<string | null>(initialDiagnosisText);
  const [premiumDiagnosisText, setPremiumDiagnosisText] = useState<string | null>(initialPremiumDiagnosisText);
  const [premiumModels, setPremiumModels] = useState<string[]>([]);
  const [defaultPremiumModel, setDefaultPremiumModel] = useState<string>("");
  const [activeTab, setActiveTab] = useState("summary");

  useEffect(() => {
    if (!premiumLlmEnabled) return;
    getPremiumModels()
      .then((data) => {
        setPremiumModels(data.models);
        setDefaultPremiumModel(data.default);
      })
      .catch((err: unknown) => {
        console.warn("Failed to fetch premium models:", err);
      });
  }, [premiumLlmEnabled]);

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
      <Tabs defaultValue="summary" onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="detailed">Detailed</TabsTrigger>
          <TabsTrigger value="rag">RAG</TabsTrigger>
          <TabsTrigger value="ai_diagnosis">AI Diagnostic Result</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
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
          {premiumLlmEnabled ? (
            <Tabs defaultValue="local">
              <TabsList className="mb-4">
                <TabsTrigger value="local">Local LLM</TabsTrigger>
                <TabsTrigger value="premium">Cloud LLM (OpenRouter)</TabsTrigger>
              </TabsList>

              <TabsContent value="local" forceMount className="space-y-6 data-[state=inactive]:hidden">
                <AIDiagnosisView
                  sessionId={sessionId}
                  initialDiagnosisText={diagnosisText}
                  onDiagnosisGenerated={setDiagnosisText}
                  provider="local"
                />
                <FeedbackForm sessionId={sessionId} feedbackTab="ai_diagnosis" />
              </TabsContent>

              <TabsContent value="premium" forceMount className="space-y-6 data-[state=inactive]:hidden">
                <AIDiagnosisView
                  sessionId={sessionId}
                  initialDiagnosisText={premiumDiagnosisText}
                  onDiagnosisGenerated={setPremiumDiagnosisText}
                  provider="premium"
                  availableModels={premiumModels}
                  defaultModel={defaultPremiumModel}
                />
                <FeedbackForm sessionId={sessionId} feedbackTab="premium_diagnosis" />
              </TabsContent>
            </Tabs>
          ) : (
            <>
              <AIDiagnosisView
                sessionId={sessionId}
                initialDiagnosisText={diagnosisText}
                onDiagnosisGenerated={setDiagnosisText}
                provider="local"
              />
              <FeedbackForm sessionId={sessionId} feedbackTab="ai_diagnosis" />
            </>
          )}
        </TabsContent>

        <TabsContent value="history" forceMount className="space-y-6 data-[state=inactive]:hidden">
          <Tabs defaultValue="local_history">
            <TabsList className="mb-4">
              <TabsTrigger value="local_history">Local Model</TabsTrigger>
              <TabsTrigger value="cloud_history">Cloud Model</TabsTrigger>
              <TabsTrigger value="feedback_history">Feedback</TabsTrigger>
            </TabsList>

            <TabsContent value="local_history" forceMount className="space-y-6 data-[state=inactive]:hidden">
              <DiagnosisHistoryView
                sessionId={sessionId}
                active={activeTab === "history"}
                provider="local"
              />
            </TabsContent>

            <TabsContent value="cloud_history" forceMount className="space-y-6 data-[state=inactive]:hidden">
              <DiagnosisHistoryView
                sessionId={sessionId}
                active={activeTab === "history"}
                provider="premium"
              />
            </TabsContent>

            <TabsContent value="feedback_history" forceMount className="space-y-6 data-[state=inactive]:hidden">
              <FeedbackHistoryView
                sessionId={sessionId}
                active={activeTab === "history"}
              />
            </TabsContent>
          </Tabs>
        </TabsContent>
      </Tabs>
    </div>
  );
}
