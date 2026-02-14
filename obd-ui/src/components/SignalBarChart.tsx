"use client";

import { useMemo, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { ValueStatistics } from "@/lib/types";
import { groupSignalsByUnit, signalDisplayName } from "@/lib/utils";
import { Select } from "@/components/ui/select";

interface SignalBarChartProps {
  valueStatistics: ValueStatistics;
}

export function SignalBarChart({ valueStatistics }: SignalBarChartProps) {
  const unitGroups = useMemo(
    () => groupSignalsByUnit(valueStatistics.column_units),
    [valueStatistics.column_units],
  );
  const unitOptions = Object.keys(unitGroups);
  const [selectedUnit, setSelectedUnit] = useState(unitOptions[0] || "");

  const chartData = useMemo(() => {
    const signals = unitGroups[selectedUnit] || [];
    return signals.map((signal) => {
      const stats = valueStatistics.stats[signal];
      return {
        signal: signalDisplayName(signal),
        min: stats?.min ?? 0,
        mean: stats?.mean ?? 0,
        max: stats?.max ?? 0,
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
        <BarChart data={chartData} margin={{ top: 5, right: 20, left: 10, bottom: 60 }}>
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
          <Tooltip />
          <Legend />
          <Bar dataKey="min" fill="#93c5fd" name="Min" />
          <Bar dataKey="mean" fill="#3b82f6" name="Mean" />
          <Bar dataKey="max" fill="#1d4ed8" name="Max" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
