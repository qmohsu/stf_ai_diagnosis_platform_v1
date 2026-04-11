"use client";

import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { streamAgentDiagnosis } from "@/lib/api";
import type {
  AgentDoneEvent,
  AgentErrorEvent,
  AgentToolCallEvent,
  AgentToolResultEvent,
  ToolInvocation,
} from "@/lib/types";
import { IterationProgress } from "@/components/IterationProgress";
import { ToolCallCard } from "@/components/ToolCallCard";

interface AgentDiagnosisViewProps {
  sessionId: string;
  initialDiagnosisText: string | null;
  onDiagnosisGenerated?: (text: string) => void;
  onDiagnosisHistoryIdChanged?: (id: string | null) => void;
}

export function AgentDiagnosisView({
  sessionId,
  initialDiagnosisText,
  onDiagnosisGenerated,
  onDiagnosisHistoryIdChanged,
}: AgentDiagnosisViewProps) {
  const { t, i18n } = useTranslation();

  const [streaming, setStreaming] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [done, setDone] = useState(!!initialDiagnosisText);
  const [error, setError] = useState<AgentErrorEvent | null>(null);
  const [diagnosisText, setDiagnosisText] = useState<string>(
    initialDiagnosisText ?? "",
  );

  // Agent-specific state
  const [toolInvocations, setToolInvocations] = useState<ToolInvocation[]>([]);
  const [currentIteration, setCurrentIteration] = useState(0);
  const [maxIterations, setMaxIterations] = useState<number | null>(null);
  const [autonomyTier, setAutonomyTier] = useState<number | null>(null);
  const [autonomyStrategy, setAutonomyStrategy] = useState<string | null>(null);

  // Refs for accumulating text (Tier 0 fallback) and
  // tracking whether onDone already fired (avoids stale closure).
  const textRef = useRef("");
  const historyIdRef = useRef<string | null>(null);
  const doneRef = useRef(false);

  const handleGenerate = useCallback(
    async (force?: boolean) => {
      setStreaming(true);
      setDone(false);
      setError(null);
      setStatusMsg(t("agent.connecting"));
      setDiagnosisText("");
      setToolInvocations([]);
      setCurrentIteration(0);
      setMaxIterations(null);
      setAutonomyTier(null);
      setAutonomyStrategy(null);
      textRef.current = "";
      historyIdRef.current = null;
      doneRef.current = false;

      try {
        await streamAgentDiagnosis(
          sessionId,
          {
            onToken: (token: string) => {
              setStatusMsg(null);
              textRef.current += token;
              setDiagnosisText(textRef.current);
            },

            onToolCall: (data: AgentToolCallEvent) => {
              setStatusMsg(null);
              setCurrentIteration(data.iteration);
              const inv: ToolInvocation = {
                id: data.tool_call_id,
                name: data.name,
                input: data.input,
                iteration: data.iteration,
                status: "calling",
              };
              setToolInvocations((prev) => [...prev, inv]);
            },

            onToolResult: (data: AgentToolResultEvent) => {
              setToolInvocations((prev) => {
                const idx = prev.findIndex(
                  (inv) =>
                    inv.name === data.name &&
                    inv.iteration === data.iteration &&
                    inv.status === "calling",
                );
                if (idx === -1) return prev;
                const updated = [...prev];
                updated[idx] = {
                  ...updated[idx],
                  result: {
                    output: data.output,
                    duration_ms: data.duration_ms,
                    is_error: data.is_error,
                  },
                  status: data.is_error ? "error" : "done",
                };
                return updated;
              });
            },

            onDone: (data: AgentDoneEvent) => {
              doneRef.current = true;
              historyIdRef.current = data.diagnosis_history_id;
              setStatusMsg(null);
              setDiagnosisText(data.text);
              setAutonomyTier(data.autonomy_tier);
              setAutonomyStrategy(data.autonomy_strategy);
              setCurrentIteration(data.iterations);
              setDone(true);
              setStreaming(false);
              onDiagnosisGenerated?.(data.text);
              onDiagnosisHistoryIdChanged?.(data.diagnosis_history_id);
            },

            onError: (data: AgentErrorEvent) => {
              setStatusMsg(null);
              setError(data);
              setStreaming(false);
            },

            onCached: (data) => {
              historyIdRef.current = data.diagnosis_history_id;
              setStatusMsg(null);
              setDiagnosisText(data.text);
              setDone(true);
              setStreaming(false);
              onDiagnosisGenerated?.(data.text);
              onDiagnosisHistoryIdChanged?.(data.diagnosis_history_id);
            },

            onStatus: (message: string) => {
              setStatusMsg(message);
            },

            onSessionStart: (data) => {
              setMaxIterations(data.max_iterations);
            },
          },
          { force, locale: i18n.language },
        );

        // Stream ended (connection closed without done event)
        setStreaming(false);
        if (textRef.current && !doneRef.current) {
          doneRef.current = true;
          setDone(true);
          onDiagnosisGenerated?.(textRef.current);
          onDiagnosisHistoryIdChanged?.(historyIdRef.current);
        }
      } catch (err) {
        setError({
          error_type: "connection",
          message: err instanceof Error ? err.message : t("agent.generateFailed"),
        });
        setStreaming(false);
        setStatusMsg(null);
      }
    },
    [sessionId, onDiagnosisGenerated, onDiagnosisHistoryIdChanged, t, i18n.language],
  );

  const hasToolCalls = toolInvocations.length > 0;

  // Not started yet — show generate button
  if (!streaming && !diagnosisText && !error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{t("agent.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {t("agent.description")}
          </p>
          <Button onClick={() => handleGenerate()} className="w-full">
            {t("agent.generate")}
          </Button>
        </CardContent>
      </Card>
    );
  }

  // Streaming or completed
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">{t("agent.title")}</CardTitle>
          {streaming && (
            <span className="text-xs text-muted-foreground animate-pulse">
              {t("agent.generating")}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Error alert */}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error.message}</AlertDescription>
          </Alert>
        )}

        {/* Status spinner while waiting for first event */}
        {streaming && statusMsg && !hasToolCalls && !diagnosisText && (
          <div className="flex items-center gap-3 py-8 justify-center text-sm text-muted-foreground">
            <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <span>{statusMsg}</span>
          </div>
        )}

        {/* Iteration progress + autonomy tier badge */}
        {(hasToolCalls || autonomyTier !== null) && (
          <IterationProgress
            currentIteration={currentIteration}
            maxIterations={maxIterations}
            autonomyTier={autonomyTier}
            autonomyStrategy={autonomyStrategy}
            streaming={streaming}
          />
        )}

        {/* Tool call cards */}
        {hasToolCalls && (
          <div className="space-y-2">
            {toolInvocations.map((inv, idx) => (
              <ToolCallCard
                key={inv.id}
                invocation={inv}
                defaultExpanded={
                  streaming && idx === toolInvocations.length - 1
                }
              />
            ))}
          </div>
        )}

        {/* Diagnosis text */}
        {diagnosisText && (
          <pre className="whitespace-pre-wrap text-sm leading-relaxed font-sans">
            {diagnosisText}
            {streaming && (
              <span className="inline-block w-2 h-4 bg-foreground animate-pulse align-text-bottom" />
            )}
          </pre>
        )}

        {/* Completed summary */}
        {done && currentIteration > 0 && (
          <p className="text-xs text-muted-foreground">
            {t("agent.iterationsCompleted", { count: currentIteration })}
          </p>
        )}

        {/* Regenerate button */}
        {done && (
          <Button
            variant="outline"
            className="w-full"
            onClick={() => handleGenerate(true)}
          >
            {t("agent.regenerateShort")}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
