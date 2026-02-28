"use client";

import { useCallback, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { streamDiagnosis, streamPremiumDiagnosis } from "@/lib/api";

interface AIDiagnosisViewProps {
  sessionId: string;
  initialDiagnosisText: string | null;
  onDiagnosisGenerated?: (text: string) => void;
  provider?: "local" | "premium";
}

export function AIDiagnosisView({ sessionId, initialDiagnosisText, onDiagnosisGenerated, provider = "local" }: AIDiagnosisViewProps) {
  const isPremium = provider === "premium";
  const streamFn = isPremium ? streamPremiumDiagnosis : streamDiagnosis;
  const [diagnosisText, setDiagnosisText] = useState<string>(initialDiagnosisText ?? "");
  const [streaming, setStreaming] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [done, setDone] = useState(!!initialDiagnosisText);
  const [error, setError] = useState<string | null>(null);
  const textRef = useRef("");

  const handleGenerate = useCallback(async (force?: boolean) => {
    setStreaming(true);
    setDone(false);
    setError(null);
    setStatusMsg("Connecting...");
    setDiagnosisText("");
    textRef.current = "";

    try {
      await streamFn(
        sessionId,
        (token) => {
          setStatusMsg(null);
          textRef.current += token;
          setDiagnosisText(textRef.current);
        },
        (fullText) => {
          setStatusMsg(null);
          setDone(true);
          setStreaming(false);
          onDiagnosisGenerated?.(fullText);
        },
        (err) => {
          setStatusMsg(null);
          setError(err);
          setStreaming(false);
        },
        (status) => {
          setStatusMsg(status);
        },
        force,
      );
      // Stream ended (connection closed)
      setStreaming(false);
      if (textRef.current) {
        setDone(true);
        onDiagnosisGenerated?.(textRef.current);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate diagnosis");
      setStreaming(false);
      setStatusMsg(null);
    }
  }, [sessionId, streamFn, onDiagnosisGenerated]);

  // Not started yet — show generate button
  if (!streaming && !diagnosisText) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">
            {isPremium ? "Premium AI Diagnostic Result" : "AI Diagnostic Result"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {isPremium
              ? "Generate a diagnostic report using the premium cloud LLM (Claude). Cloud API usage fees apply."
              : "Generate an AI-powered diagnostic report using the parsed OBD data and retrieved technical context. This may take 1-2 minutes on first generation."}
          </p>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Button onClick={() => handleGenerate()} className="w-full">
            {isPremium ? "Generate Premium Diagnosis" : "Generate AI Diagnosis"}
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
            {isPremium ? "Premium AI Diagnostic Result" : "AI Diagnostic Result"}
          </CardTitle>
          {streaming && (
            <span className="text-xs text-muted-foreground animate-pulse">
              Generating...
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

        {done && (
          <Button variant="outline" className="w-full mt-4" onClick={() => handleGenerate(true)}>
            {isPremium ? "Regenerate Premium Diagnosis" : "Regenerate Diagnosis"}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
