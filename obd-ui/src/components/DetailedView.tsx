"use client";

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
  return (
    <div className="space-y-8">
      {/* Value Statistics Section */}
      <section>
        <h2 className="mb-4 text-xl font-semibold">Value Statistics</h2>
        <ValueStatisticsPanel valueStatistics={data.value_statistics} />
      </section>

      {/* Anomaly Detection Section */}
      <section>
        <h2 className="mb-4 text-xl font-semibold">Anomaly Detection</h2>
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-lg">Anomaly Timeline</CardTitle>
          </CardHeader>
          <CardContent>
            <AnomalyTimeline events={data.anomaly_events} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">
              Anomaly Events ({data.anomaly_events.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            <AnomalyEventList events={data.anomaly_events} />
          </CardContent>
        </Card>
      </section>

      {/* Clue Details Section */}
      <section>
        <h2 className="mb-4 text-xl font-semibold">Clue Details</h2>
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">
              Diagnostic Clue Details ({data.clue_details.length})
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
