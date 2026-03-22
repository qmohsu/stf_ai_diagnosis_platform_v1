"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Select } from "@/components/ui/select";
import { streamDiagnosis, streamPremiumDiagnosis } from "@/lib/api";

interface AIDiagnosisViewProps {
  sessionId: string;
  initialDiagnosisText: string | null;
  onDiagnosisGenerated?: (text: string) => void;
  onDiagnosisHistoryIdChanged?: (id: string | null) => void;
  provider?: "local" | "premium";
  availableModels?: string[];
  defaultModel?: string;
}

export function AIDiagnosisView({
  sessionId,
  initialDiagnosisText,
  onDiagnosisGenerated,
  onDiagnosisHistoryIdChanged,
  provider = "local",
  availableModels,
  defaultModel,
}: AIDiagnosisViewProps) {
  const { t, i18n } = useTranslation();
  const isPremium = provider === "premium";
  const [selectedModel, setSelectedModel] = useState<string>(defaultModel ?? "");
  const [diagnosisText, setDiagnosisText] = useState<string>(initialDiagnosisText ?? "");

  // Sync selectedModel when defaultModel loads asynchronously
  useEffect(() => {
    if (defaultModel && !selectedModel) {
      setSelectedModel(defaultModel);
    }
  }, [defaultModel, selectedModel]);
  const [streaming, setStreaming] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [done, setDone] = useState(!!initialDiagnosisText);
  const [error, setError] = useState<string | null>(null);
  const textRef = useRef("");
  const historyIdRef = useRef<string | null>(null);

  const handleGenerate = useCallback(async (force?: boolean) => {
    setStreaming(true);
    setDone(false);
    setError(null);
    setStatusMsg(t("diagnosis.connecting"));
    setDiagnosisText("");
    textRef.current = "";
    historyIdRef.current = null;

    const onToken = (token: string) => {
      setStatusMsg(null);
      textRef.current += token;
      setDiagnosisText(textRef.current);
    };
    const onDone = (fullText: string, historyId: string | null) => {
      historyIdRef.current = historyId;
      setStatusMsg(null);
      setDone(true);
      setStreaming(false);
      onDiagnosisGenerated?.(fullText);
      onDiagnosisHistoryIdChanged?.(historyId);
    };
    const onError = (err: string) => {
      setStatusMsg(null);
      setError(err);
      setStreaming(false);
    };
    const onStatus = (status: string) => {
      setStatusMsg(status);
    };

    try {
      if (isPremium) {
        await streamPremiumDiagnosis(
          sessionId, onToken, onDone, onError, onStatus,
          force, selectedModel || undefined, i18n.language,
        );
      } else {
        await streamDiagnosis(
          sessionId, onToken, onDone, onError, onStatus,
          force, i18n.language,
        );
      }
      // Stream ended (connection closed)
      setStreaming(false);
      if (textRef.current) {
        setDone(true);
        onDiagnosisGenerated?.(textRef.current);
        onDiagnosisHistoryIdChanged?.(historyIdRef.current);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("diagnosis.generateFailed"));
      setStreaming(false);
      setStatusMsg(null);
    }
  }, [sessionId, isPremium, selectedModel, onDiagnosisGenerated, onDiagnosisHistoryIdChanged, t, i18n.language]);

  // Not started yet — show generate button
  if (!streaming && !diagnosisText) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">
            {isPremium ? t("diagnosis.cloudTitle") : t("diagnosis.title")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {isPremium ? t("diagnosis.cloudDescription") : t("diagnosis.localDescription")}
          </p>
          {isPremium && availableModels && availableModels.length > 0 && (
            <div className="space-y-2">
              <label htmlFor="model-select" className="text-sm font-medium">
                {t("diagnosis.model")}
              </label>
              <Select
                id="model-select"
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="w-full"
              >
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </div>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Button onClick={() => handleGenerate()} className="w-full">
            {isPremium ? t("diagnosis.generateCloud") : t("diagnosis.generateLocal")}
          </Button>
        </CardContent>
      </Card>
    );
  }

  // Streaming or completed — show progressive text
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">
            {isPremium ? t("diagnosis.cloudTitle") : t("diagnosis.title")}
          </CardTitle>
          {streaming && (
            <span className="text-xs text-muted-foreground animate-pulse">
              {t("diagnosis.generating")}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {error && (
          <Alert variant="destructive" className="mb-4">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* Status message while waiting for LLM first token */}
        {streaming && statusMsg && !diagnosisText && (
          <div className="flex items-center gap-3 py-8 justify-center text-sm text-muted-foreground">
            <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <span>{statusMsg}</span>
          </div>
        )}

        {diagnosisText && (
          <pre className="whitespace-pre-wrap text-sm leading-relaxed font-sans">
            {diagnosisText}
            {streaming && <span className="inline-block w-2 h-4 bg-foreground animate-pulse align-text-bottom" />}
          </pre>
        )}

        {done && isPremium && selectedModel && (
          <p className="text-xs text-muted-foreground mt-2">
            {t("diagnosis.modelUsed", { model: selectedModel })}
          </p>
        )}

        {done && (
          <div className="flex items-center gap-2 mt-4">
            {isPremium && availableModels && availableModels.length > 0 && (
              <Select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="flex-1"
              >
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            )}
            <Button variant="outline" className={isPremium && availableModels?.length ? "" : "w-full"} onClick={() => handleGenerate(true)}>
              {isPremium ? t("diagnosis.regenerateShort") : t("diagnosis.regenerate")}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
