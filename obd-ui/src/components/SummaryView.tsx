"use client";

import { useTranslation } from "react-i18next";
import type { LogSummaryV2 } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PIDSummaryTable } from "@/components/PIDSummaryTable";
import { DiagnosticCluesList } from "@/components/DiagnosticCluesList";

interface SummaryViewProps {
  data: LogSummaryV2;
}

export function SummaryView({ data }: SummaryViewProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      {/* PID Summary */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{t("summary.pidSummary")}</CardTitle>
        </CardHeader>
        <CardContent>
          <PIDSummaryTable pidSummary={data.pid_summary} />
        </CardContent>
      </Card>

      {/* DTC Codes */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{t("summary.dtcCodes")}</CardTitle>
        </CardHeader>
        <CardContent>
          {data.dtc_codes.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {data.dtc_codes.map((code) => (
                <Badge key={code} variant="destructive">
                  {code}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t("summary.noDtc")}</p>
          )}
        </CardContent>
      </Card>

      {/* Diagnostic Clues */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{t("summary.diagnosticClues")}</CardTitle>
        </CardHeader>
        <CardContent>
          <DiagnosticCluesList clues={data.diagnostic_clues} />
        </CardContent>
      </Card>
    </div>
  );
}
