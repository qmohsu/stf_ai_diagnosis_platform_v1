"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ValueStatistics } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SignalBarChart } from "@/components/SignalBarChart";
import { SignalBoxPlot } from "@/components/SignalBoxPlot";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatNumber, signalDisplayName } from "@/lib/utils";

interface ValueStatisticsPanelProps {
  valueStatistics: ValueStatistics;
}

export function ValueStatisticsPanel({ valueStatistics }: ValueStatisticsPanelProps) {
  const { t } = useTranslation();
  const [showTable, setShowTable] = useState(false);
  const signals = Object.keys(valueStatistics.stats);

  return (
    <div className="space-y-6">
      {/* Bar Chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{t("statistics.barChart")}</CardTitle>
        </CardHeader>
        <CardContent>
          <SignalBarChart valueStatistics={valueStatistics} />
        </CardContent>
      </Card>

      {/* Box Plot */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{t("statistics.boxPlot")}</CardTitle>
        </CardHeader>
        <CardContent>
          <SignalBoxPlot valueStatistics={valueStatistics} />
        </CardContent>
      </Card>

      {/* Full Stats Table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-lg">{t("statistics.fullTable")}</CardTitle>
          <button
            type="button"
            onClick={() => setShowTable(!showTable)}
            className="text-sm text-primary hover:underline"
          >
            {showTable ? t("statistics.hideTable") : t("statistics.showTable")}
          </button>
        </CardHeader>
        {showTable && (
          <CardContent>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("statistics.signal")}</TableHead>
                    <TableHead>{t("statistics.unit")}</TableHead>
                    <TableHead className="text-right">{t("statistics.mean")}</TableHead>
                    <TableHead className="text-right">{t("statistics.std")}</TableHead>
                    <TableHead className="text-right">{t("statistics.min")}</TableHead>
                    <TableHead className="text-right">{t("statistics.p5")}</TableHead>
                    <TableHead className="text-right">{t("statistics.p25")}</TableHead>
                    <TableHead className="text-right">{t("statistics.p50")}</TableHead>
                    <TableHead className="text-right">{t("statistics.p75")}</TableHead>
                    <TableHead className="text-right">{t("statistics.p95")}</TableHead>
                    <TableHead className="text-right">{t("statistics.max")}</TableHead>
                    <TableHead className="text-right">{t("statistics.entropy")}</TableHead>
                    <TableHead className="text-right">{t("statistics.count")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {signals.map((sig) => {
                    const s = valueStatistics.stats[sig];
                    return (
                      <TableRow key={sig}>
                        <TableCell className="font-medium text-xs">{signalDisplayName(sig)}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {valueStatistics.column_units[sig] || "-"}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.mean)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.std)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.min)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.p5)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.p25)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.p50)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.p75)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.p95)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.max)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(s.entropy)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{s.valid_count}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        )}
      </Card>
    </div>
  );
}
