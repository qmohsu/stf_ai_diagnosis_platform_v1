"use client";

import { AlertCircle } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

interface DiagnosticCluesListProps {
  clues: string[];
}

export function DiagnosticCluesList({ clues }: DiagnosticCluesListProps) {
  if (clues.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No diagnostic clues generated.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {clues.map((clue, i) => (
        <Card key={i} className="border-l-4 border-l-amber-400">
          <CardContent className="flex items-start gap-3 p-4">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
            <p className="text-sm">{clue}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
