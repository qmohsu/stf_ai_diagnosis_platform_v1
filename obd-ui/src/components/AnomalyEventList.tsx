"use client";

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AnomalyEvent } from "@/lib/types";
import { AnomalyEventCard } from "@/components/AnomalyEventCard";
import { Select } from "@/components/ui/select";

interface AnomalyEventListProps {
  events: AnomalyEvent[];
}

export function AnomalyEventList({ events }: AnomalyEventListProps) {
  const { t } = useTranslation();
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
    return <p className="text-sm text-muted-foreground">{t("anomaly.noEvents")}</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium">{t("anomaly.sort")}</label>
          <Select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as "score" | "time")}
            className="w-32"
          >
            <option value="score">{t("anomaly.score")}</option>
            <option value="time">{t("anomaly.time")}</option>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium">{t("anomaly.severity")}</label>
          <Select
            value={filterSeverity}
            onChange={(e) => setFilterSeverity(e.target.value)}
            className="w-32"
          >
            <option value="all">{t("anomaly.all")}</option>
            <option value="high">{t("anomaly.high")}</option>
            <option value="medium">{t("anomaly.medium")}</option>
            <option value="low">{t("anomaly.low")}</option>
          </Select>
        </div>
        <span className="self-center text-xs text-muted-foreground">
          {t("anomaly.eventsCount", { filtered: filtered.length, total: events.length })}
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
