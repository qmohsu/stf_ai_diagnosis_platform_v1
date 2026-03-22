"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
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
  initialDiagnosisHistoryId?: string | null;
  initialPremiumDiagnosisHistoryId?: string | null;
}

export function AnalysisLayout({
  sessionId,
  data,
  parsedSummary,
  diagnosisText: initialDiagnosisText,
  premiumDiagnosisText: initialPremiumDiagnosisText,
  premiumLlmEnabled,
  initialDiagnosisHistoryId,
  initialPremiumDiagnosisHistoryId,
}: AnalysisLayoutProps) {
  const { t } = useTranslation();
  const [diagnosisText, setDiagnosisText] = useState<string | null>(initialDiagnosisText);
  const [premiumDiagnosisText, setPremiumDiagnosisText] = useState<string | null>(initialPremiumDiagnosisText);
  const [diagnosisHistoryId, setDiagnosisHistoryId] = useState<string | null>(initialDiagnosisHistoryId ?? null);
  const [premiumDiagnosisHistoryId, setPremiumDiagnosisHistoryId] = useState<string | null>(initialPremiumDiagnosisHistoryId ?? null);
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
          <h2 className="text-2xl font-bold">{t("analysis.results")}</h2>
          <Badge variant="secondary" className="font-mono">
            {data.vehicle_id}
          </Badge>
        </div>
        <div className="flex items-center gap-4 text-sm text-muted-foreground flex-wrap">
          <span>
            {data.time_range.start} — {data.time_range.end}
          </span>
          <span>{t("analysis.duration", { duration: formatDuration(data.time_range.duration_seconds) })}</span>
          <span>{t("analysis.samples", { count: data.time_range.sample_count })}</span>
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
          <TabsTrigger value="summary">{t("tabs.summary")}</TabsTrigger>
          <TabsTrigger value="detailed">{t("tabs.detailed")}</TabsTrigger>
          <TabsTrigger value="rag">{t("tabs.rag")}</TabsTrigger>
          <TabsTrigger value="ai_diagnosis">{t("tabs.aiDiagnosis")}</TabsTrigger>
          <TabsTrigger value="history">{t("tabs.history")}</TabsTrigger>
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
                <TabsTrigger value="local">{t("tabs.localLlm")}</TabsTrigger>
                <TabsTrigger value="premium">{t("tabs.cloudLlm")}</TabsTrigger>
              </TabsList>

              <TabsContent value="local" forceMount className="space-y-6 data-[state=inactive]:hidden">
                <AIDiagnosisView
                  sessionId={sessionId}
                  initialDiagnosisText={diagnosisText}
                  onDiagnosisGenerated={setDiagnosisText}
                  onDiagnosisHistoryIdChanged={setDiagnosisHistoryId}
                  provider="local"
                />
                <FeedbackForm sessionId={sessionId} feedbackTab="ai_diagnosis" diagnosisHistoryId={diagnosisHistoryId} />
              </TabsContent>

              <TabsContent value="premium" forceMount className="space-y-6 data-[state=inactive]:hidden">
                <AIDiagnosisView
                  sessionId={sessionId}
                  initialDiagnosisText={premiumDiagnosisText}
                  onDiagnosisGenerated={setPremiumDiagnosisText}
                  onDiagnosisHistoryIdChanged={setPremiumDiagnosisHistoryId}
                  provider="premium"
                  availableModels={premiumModels}
                  defaultModel={defaultPremiumModel}
                />
                <FeedbackForm sessionId={sessionId} feedbackTab="premium_diagnosis" diagnosisHistoryId={premiumDiagnosisHistoryId} />
              </TabsContent>
            </Tabs>
          ) : (
            <>
              <AIDiagnosisView
                sessionId={sessionId}
                initialDiagnosisText={diagnosisText}
                onDiagnosisGenerated={setDiagnosisText}
                onDiagnosisHistoryIdChanged={setDiagnosisHistoryId}
                provider="local"
              />
              <FeedbackForm sessionId={sessionId} feedbackTab="ai_diagnosis" diagnosisHistoryId={diagnosisHistoryId} />
            </>
          )}
        </TabsContent>

        <TabsContent value="history" forceMount className="space-y-6 data-[state=inactive]:hidden">
          <Tabs defaultValue="local_history">
            <TabsList className="mb-4">
              <TabsTrigger value="local_history">{t("tabs.localModel")}</TabsTrigger>
              <TabsTrigger value="cloud_history">{t("tabs.cloudModel")}</TabsTrigger>
              <TabsTrigger value="feedback_history">{t("tabs.feedback")}</TabsTrigger>
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
