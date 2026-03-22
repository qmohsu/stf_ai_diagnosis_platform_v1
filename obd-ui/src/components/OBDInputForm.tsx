"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { FileDropZone } from "@/components/FileDropZone";
import { analyzeOBDLog } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";

export function OBDInputForm() {
  const router = useRouter();
  const { t } = useTranslation();
  const { username, isLoading: authLoading } = useAuth();
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"paste" | "file">("paste");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAnalyze = async () => {
    if (!text.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const response = await analyzeOBDLog(text);
      router.push(`/analysis/${response.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("input.analysisFailed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="mx-auto max-w-4xl">
      <CardHeader>
        <CardTitle>{t("input.title")}</CardTitle>
        <CardDescription>
          {t("input.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <Button
            variant={mode === "paste" ? "default" : "outline"}
            size="sm"
            onClick={() => setMode("paste")}
          >
            {t("input.pasteText")}
          </Button>
          <Button
            variant={mode === "file" ? "default" : "outline"}
            size="sm"
            onClick={() => setMode("file")}
          >
            {t("input.uploadFile")}
          </Button>
        </div>

        {mode === "paste" ? (
          <Textarea
            placeholder={t("input.placeholder")}
            className="min-h-[300px] font-mono text-xs"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        ) : (
          <FileDropZone onFileContent={(content) => { setText(content); setMode("paste"); }} />
        )}

        {text && (
          <p className="text-xs text-muted-foreground">
            {t("input.charCount", { length: text.length.toLocaleString(), lines: Math.ceil(text.split("\n").length) })}
          </p>
        )}

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Button
          onClick={handleAnalyze}
          disabled={!text.trim() || loading}
          className="w-full"
          size="lg"
        >
          {loading ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("input.analyzing")}
            </>
          ) : (
            t("input.analyze")
          )}
        </Button>

        {!authLoading && username && (
          <div className="text-center">
            <Link
              href="/sessions"
              className="text-sm text-muted-foreground hover:text-primary hover:underline"
            >
              {t("input.viewPastSessions")}
            </Link>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
