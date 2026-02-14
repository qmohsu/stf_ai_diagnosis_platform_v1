"use client";

import { useMemo, useState } from "react";
import {
  ComposedChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ErrorBar,
} from "recharts";
import type { ValueStatistics } from "@/lib/types";
import { groupSignalsByUnit, signalDisplayName } from "@/lib/utils";
import { Select } from "@/components/ui/select";

interface SignalBoxPlotProps {
  valueStatistics: ValueStatistics;
}

export function SignalBoxPlot({ valueStatistics }: SignalBoxPlotProps) {
  const unitGroups = useMemo(
    () => groupSignalsByUnit(valueStatistics.column_units),
    [valueStatistics.column_units],
  );
  const unitOptions = Object.keys(unitGroups);
  const [selectedUnit, setSelectedUnit] = useState(unitOptions[0] || "");

  const chartData = useMemo(() => {
    const signals = unitGroups[selectedUnit] || [];
    return signals.map((signal) => {
      const s = valueStatistics.stats[signal];
      if (!s) return { signal: signalDisplayName(signal), p50: 0, iqr: 0, lowerWhisker: 0, upperWhisker: 0 };
      const p25 = s.p25 ?? 0;
      const p75 = s.p75 ?? 0;
      const p50 = s.p50 ?? 0;
      return {
        signal: signalDisplayName(signal),
        // Use p25 as base, bar height = IQR (p25 to p75)
        base: p25,
        iqr: p75 - p25,
        p50,
        // Whiskers
        p5: s.p5 ?? 0,
        p95: s.p95 ?? 0,
        lowerWhisker: p25 - (s.p5 ?? 0),
        upperWhisker: (s.p95 ?? 0) - p75,
      };
    });
  }, [selectedUnit, unitGroups, valueStatistics.stats]);

  if (unitOptions.length === 0) return null;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium">Unit group:</label>
        <Select
          value={selectedUnit}
          onChange={(e) => setSelectedUnit(e.target.value)}
          className="w-48"
        >
          {unitOptions.map((unit) => (
            <option key={unit} value={unit}>
              {unit} ({unitGroups[unit].length} signals)
            </option>
          ))}
        </Select>
      </div>

      <ResponsiveContainer width="100%" height={350}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 20, left: 10, bottom: 60 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="signal"
            angle={-35}
            textAnchor="end"
            interval={0}
            tick={{ fontSize: 11 }}
            height={80}
          />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload;
              return (
                <div className="rounded border bg-white p-2 text-xs shadow">
                  <p className="font-semibold">{d.signal}</p>
                  <p>P5: {d.p5?.toFixed(2)}</p>
                  <p>P25: {d.base?.toFixed(2)}</p>
                  <p>P50: {d.p50?.toFixed(2)}</p>
                  <p>P75: {(d.base + d.iqr)?.toFixed(2)}</p>
                  <p>P95: {d.p95?.toFixed(2)}</p>
                </div>
              );
            }}
          />
          {/* Invisible base bar to offset */}
          <Bar dataKey="base" stackId="box" fill="transparent" />
          {/* IQR box */}
          <Bar dataKey="iqr" stackId="box" fill="#60a5fa" stroke="#3b82f6" strokeWidth={1}>
            <ErrorBar dataKey="upperWhisker" width={4} strokeWidth={1.5} stroke="#1e40af" direction="y" />
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
      <p className="text-xs text-muted-foreground">
        Box: P25-P75 (IQR). Whiskers: P5-P95 range.
      </p>
    </div>
  );
}
