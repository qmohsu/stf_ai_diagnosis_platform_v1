"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AnalysisLayout } from "@/components/AnalysisLayout";
import { getAnalysisSession } from "@/lib/api";
import type { OBDAnalysisResponse } from "@/lib/types";

function BackToSessions() {
  const { t } = useTranslation();
  return (
    <Link
      href="/sessions"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="h-4 w-4" />
      {t("sessions.backToSessions")}
    </Link>
  );
}

export default function AnalysisPage() {
  const params = useParams();
  const sessionId = params.sessionId as string;
  const { t } = useTranslation();
  const [data, setData] = useState<OBDAnalysisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    getAnalysisSession(sessionId)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : t("analysis.loadFailed")))
      .finally(() => setLoading(false));
  }, [sessionId, t]);

  if (loading) {
    return <AnalysisLoadingSkeleton />;
  }

  if (error) {
    return (
      <div className="mx-auto max-w-2xl mt-8 space-y-4">
        <BackToSessions />
        <Alert variant="destructive">
          <AlertTitle>{t("analysis.error")}</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    );
  }

  if (!data || data.status === "FAILED") {
    return (
      <div className="mx-auto max-w-2xl mt-8 space-y-4">
        <BackToSessions />
        <Alert variant="destructive">
          <AlertTitle>{t("analysis.failed")}</AlertTitle>
          <AlertDescription>
            {data?.error_message || t("analysis.failedDescription")}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  if (!data.result) {
    return (
      <div className="mx-auto max-w-2xl mt-8 space-y-4">
        <BackToSessions />
        <Alert>
          <AlertTitle>{t("analysis.processing")}</AlertTitle>
          <AlertDescription>{t("analysis.processingDescription")}</AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <BackToSessions />
      <AnalysisLayout
        sessionId={sessionId}
        data={data.result}
        parsedSummary={data.parsed_summary}
        diagnosisText={data.diagnosis_text}
        premiumDiagnosisText={data.premium_diagnosis_text}
        premiumLlmEnabled={data.premium_llm_enabled}
        initialDiagnosisHistoryId={data.diagnosis_history_id}
        initialPremiumDiagnosisHistoryId={data.premium_diagnosis_history_id}
      />
    </div>
  );
}

function AnalysisLoadingSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="space-y-2">
        <div className="h-8 w-64 rounded bg-muted" />
        <div className="h-4 w-96 rounded bg-muted" />
      </div>
      <div className="h-10 w-48 rounded bg-muted" />
      <div className="space-y-4">
        <div className="h-64 rounded-lg bg-muted" />
        <div className="h-48 rounded-lg bg-muted" />
        <div className="h-32 rounded-lg bg-muted" />
      </div>
    </div>
  );
}
