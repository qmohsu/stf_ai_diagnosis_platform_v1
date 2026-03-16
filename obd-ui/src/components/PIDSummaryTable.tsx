"use client";

import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  const entries = Object.entries(pidSummary);
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("summary.noPidData")}</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("pidTable.signal")}</TableHead>
          <TableHead className="text-right">{t("pidTable.min")}</TableHead>
          <TableHead className="text-right">{t("pidTable.max")}</TableHead>
          <TableHead className="text-right">{t("pidTable.mean")}</TableHead>
          <TableHead className="text-right">{t("pidTable.latest")}</TableHead>
          <TableHead>{t("pidTable.unit")}</TableHead>
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
