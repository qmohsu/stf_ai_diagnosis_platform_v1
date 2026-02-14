"use client";

import { useMemo, useState } from "react";
import type { AnomalyEvent } from "@/lib/types";
import { AnomalyEventCard } from "@/components/AnomalyEventCard";
import { Select } from "@/components/ui/select";

interface AnomalyEventListProps {
  events: AnomalyEvent[];
}

export function AnomalyEventList({ events }: AnomalyEventListProps) {
  const [sortBy, setSortBy] = useState<"score" | "time">("score");
  const [filterSeverity, setFilterSeverity] = useState<string>("all");

  const filtered = useMemo(() => {
    let result = [...events];
    if (filterSeverity !== "all") {
      result = result.filter((e) => e.severity === filterSeverity);
    }
    if (sortBy === "score") {
      result.sort((a, b) => b.score - a.score);
    } else {
      result.sort(
        (a, b) =>
          new Date(a.time_window[0]).getTime() - new Date(b.time_window[0]).getTime(),
      );
    }
    return result;
  }, [events, sortBy, filterSeverity]);

  if (events.length === 0) {
    return <p className="text-sm text-muted-foreground">No anomaly events detected.</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium">Sort:</label>
          <Select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as "score" | "time")}
            className="w-32"
          >
            <option value="score">Score</option>
            <option value="time">Time</option>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium">Severity:</label>
          <Select
            value={filterSeverity}
            onChange={(e) => setFilterSeverity(e.target.value)}
            className="w-32"
          >
            <option value="all">All</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </Select>
        </div>
        <span className="self-center text-xs text-muted-foreground">
          {filtered.length} of {events.length} events
        </span>
      </div>
      <div className="space-y-2">
        {filtered.map((event, i) => (
          <AnomalyEventCard key={i} event={event} />
        ))}
      </div>
    </div>
  );
}
