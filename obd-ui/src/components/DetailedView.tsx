"use client";

import { useTranslation } from "react-i18next";
import type { LogSummaryV2 } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ValueStatisticsPanel } from "@/components/ValueStatisticsPanel";
import { AnomalyTimeline } from "@/components/AnomalyTimeline";
import { AnomalyEventList } from "@/components/AnomalyEventList";
import { ClueDetailsPanel } from "@/components/ClueDetailsPanel";

interface DetailedViewProps {
  data: LogSummaryV2;
}

export function DetailedView({ data }: DetailedViewProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-8">
      {/* Value Statistics Section */}
      <section>
        <h2 className="mb-4 text-xl font-semibold">{t("detailed.valueStatistics")}</h2>
        <ValueStatisticsPanel valueStatistics={data.value_statistics} />
      </section>

      {/* Anomaly Detection Section */}
      <section>
        <h2 className="mb-4 text-xl font-semibold">{t("detailed.anomalyDetection")}</h2>
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-lg">{t("detailed.anomalyTimeline")}</CardTitle>
          </CardHeader>
          <CardContent>
            <AnomalyTimeline events={data.anomaly_events} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">
              {t("detailed.anomalyEvents", { count: data.anomaly_events.length })}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <AnomalyEventList events={data.anomaly_events} />
          </CardContent>
        </Card>
      </section>

      {/* Clue Details Section */}
      <section>
        <h2 className="mb-4 text-xl font-semibold">{t("detailed.clueDetails")}</h2>
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">
              {t("detailed.diagnosticClueDetails", { count: data.clue_details.length })}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ClueDetailsPanel clueDetails={data.clue_details} />
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
