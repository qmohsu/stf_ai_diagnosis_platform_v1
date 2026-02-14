"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { FileDropZone } from "@/components/FileDropZone";
import { analyzeOBDLog } from "@/lib/api";

export function OBDInputForm() {
  const router = useRouter();
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
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="mx-auto max-w-4xl">
      <CardHeader>
        <CardTitle>OBD Log Analysis</CardTitle>
        <CardDescription>
          Paste raw OBD TSV log data or upload a file to run the full diagnostic pipeline.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <Button
            variant={mode === "paste" ? "default" : "outline"}
            size="sm"
            onClick={() => setMode("paste")}
          >
            Paste Text
          </Button>
          <Button
            variant={mode === "file" ? "default" : "outline"}
            size="sm"
            onClick={() => setMode("file")}
          >
            Upload File
          </Button>
        </div>

        {mode === "paste" ? (
          <Textarea
            placeholder="Paste OBD TSV log data here..."
            className="min-h-[300px] font-mono text-xs"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        ) : (
          <FileDropZone onFileContent={(content) => { setText(content); setMode("paste"); }} />
        )}

        {text && (
          <p className="text-xs text-muted-foreground">
            {text.length.toLocaleString()} characters, ~{Math.ceil(text.split("\n").length)} lines
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
              Analyzing...
            </>
          ) : (
            "Analyze Log"
          )}
        </Button>
      </CardContent>
    </Card>
  );
}
