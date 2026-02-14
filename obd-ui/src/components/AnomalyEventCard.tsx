"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { AnomalyEvent } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatTimestamp, severityBadgeColor } from "@/lib/utils";

interface AnomalyEventCardProps {
  event: AnomalyEvent;
}

export function AnomalyEventCard({ event }: AnomalyEventCardProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Card className="border-l-4" style={{
      borderLeftColor: event.severity === "high" ? "#ef4444" : event.severity === "medium" ? "#f59e0b" : "#3b82f6",
    }}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Badge className={severityBadgeColor(event.severity)}>
                {event.severity}
              </Badge>
              <Badge variant="outline">{event.detector}</Badge>
              <Badge variant="secondary">{event.context}</Badge>
              <span className="text-xs text-muted-foreground">
                Score: {event.score.toFixed(3)}
              </span>
            </div>
            <p className="text-sm">
              {formatTimestamp(event.time_window[0])} - {formatTimestamp(event.time_window[1])}
            </p>
            <div className="flex flex-wrap gap-1">
              {event.signals.map((sig) => (
                <Badge key={sig} variant="outline" className="text-xs">
                  {sig}
                </Badge>
              ))}
            </div>
          </div>
          <button
            type="button"
            aria-label={expanded ? "Collapse details" : "Expand details"}
            onClick={() => setExpanded(!expanded)}
            className="p-1 text-muted-foreground hover:text-foreground"
          >
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
        </div>
        {expanded && (
          <p className="mt-3 text-sm text-muted-foreground whitespace-pre-wrap border-t pt-3">
            {event.pattern}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
