"use client";

import type { DiagnosticClue } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { severityBadgeColor } from "@/lib/utils";

interface ClueDetailCardProps {
  clue: DiagnosticClue;
}

const CATEGORY_COLORS: Record<string, string> = {
  statistical: "bg-purple-100 text-purple-800",
  anomaly: "bg-red-100 text-red-800",
  interaction: "bg-teal-100 text-teal-800",
  dtc: "bg-orange-100 text-orange-800",
  negative_evidence: "bg-green-100 text-green-800",
};

export function ClueDetailCard({ clue }: ClueDetailCardProps) {
  return (
    <Card>
      <CardContent className="p-4 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge className={CATEGORY_COLORS[clue.category] || "bg-gray-100 text-gray-800"}>
            {clue.category}
          </Badge>
          <Badge className={severityBadgeColor(clue.severity)}>
            {clue.severity}
          </Badge>
          <span className="text-xs font-mono text-muted-foreground">
            {clue.rule_id}
          </span>
        </div>
        <p className="text-sm">{clue.clue}</p>
        {clue.evidence.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Evidence:</p>
            <ul className="list-disc list-inside text-xs text-muted-foreground space-y-0.5">
              {clue.evidence.map((ev, i) => (
                <li key={i}>{ev}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
