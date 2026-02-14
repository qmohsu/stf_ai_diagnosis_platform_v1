"use client";

import { useMemo } from "react";
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ZAxis,
} from "recharts";
import type { AnomalyEvent } from "@/lib/types";
import { formatTimestamp } from "@/lib/utils";

interface AnomalyTimelineProps {
  events: AnomalyEvent[];
  onEventClick?: (event: AnomalyEvent) => void;
}

const SEVERITY_COLORS: Record<string, string> = {
  high: "#ef4444",
  medium: "#f59e0b",
  low: "#3b82f6",
};

export function AnomalyTimeline({ events, onEventClick }: AnomalyTimelineProps) {
  const chartData = useMemo(() => {
    return events.map((ev, idx) => ({
      x: new Date(ev.time_window[0]).getTime(),
      y: ev.score,
      z: ev.signals.length * 50 + 50,
      severity: ev.severity,
      idx,
      label: formatTimestamp(ev.time_window[0]),
    }));
  }, [events]);

  // Group by severity for color coding
  const groups = useMemo(() => {
    const result: Record<string, typeof chartData> = { high: [], medium: [], low: [] };
    chartData.forEach((d) => {
      const sev = d.severity || "low";
      if (!result[sev]) result[sev] = [];
      result[sev].push(d);
    });
    return result;
  }, [chartData]);

  if (events.length === 0) {
    return <p className="text-sm text-muted-foreground">No anomaly events detected.</p>;
  }

  return (
    <div>
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="x"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v) => formatTimestamp(new Date(v).toISOString())}
            name="Time"
            tick={{ fontSize: 11 }}
          />
          <YAxis
            dataKey="y"
            name="Score"
            domain={[0, 1]}
            tick={{ fontSize: 11 }}
            label={{ value: "Score", angle: -90, position: "insideLeft", style: { fontSize: 11 } }}
          />
          <ZAxis dataKey="z" range={[40, 400]} name="Signals" />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload;
              const ev = events[d.idx];
              return (
                <div className="rounded border bg-white p-2 text-xs shadow max-w-xs">
                  <p className="font-semibold">{d.label}</p>
                  <p>Score: {ev.score.toFixed(3)}</p>
                  <p>Severity: {ev.severity}</p>
                  <p>Detector: {ev.detector}</p>
                  <p>Context: {ev.context}</p>
                  <p>Signals: {ev.signals.join(", ")}</p>
                </div>
              );
            }}
          />
          {Object.entries(groups).map(([severity, data]) =>
            data.length > 0 ? (
              <Scatter
                key={severity}
                name={severity}
                data={data}
                fill={SEVERITY_COLORS[severity] || "#6b7280"}
                onClick={(point) => {
                  if (onEventClick && point) {
                    onEventClick(events[point.idx]);
                  }
                }}
                cursor="pointer"
              />
            ) : null,
          )}
        </ScatterChart>
      </ResponsiveContainer>
      <div className="mt-2 flex gap-4 text-xs">
        <span className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-full bg-red-500" /> High
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-full bg-amber-500" /> Medium
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-full bg-blue-500" /> Low
        </span>
        <span className="text-muted-foreground">Point size = signal count</span>
      </div>
    </div>
  );
}
