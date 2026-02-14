"use client";

import type { PIDStat } from "@/lib/types";
import { formatNumber, signalDisplayName } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface PIDSummaryTableProps {
  pidSummary: Record<string, PIDStat>;
}

export function PIDSummaryTable({ pidSummary }: PIDSummaryTableProps) {
  const entries = Object.entries(pidSummary);
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">No PID data available.</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Signal</TableHead>
          <TableHead className="text-right">Min</TableHead>
          <TableHead className="text-right">Max</TableHead>
          <TableHead className="text-right">Mean</TableHead>
          <TableHead className="text-right">Latest</TableHead>
          <TableHead>Unit</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map(([name, stat]) => (
          <TableRow key={name}>
            <TableCell className="font-medium">{signalDisplayName(name)}</TableCell>
            <TableCell className="text-right font-mono text-sm">
              {formatNumber(stat.min)}
            </TableCell>
            <TableCell className="text-right font-mono text-sm">
              {formatNumber(stat.max)}
            </TableCell>
            <TableCell className="text-right font-mono text-sm">
              {formatNumber(stat.mean)}
            </TableCell>
            <TableCell className="text-right font-mono text-sm">
              {formatNumber(stat.latest)}
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">
              {stat.unit}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
