"use client";

/**
 * /goldens/obd/[id] detail page (HARNESS-21 [2b/4]).
 *
 * Renders an OBD-lane golden entry:
 * - Question + golden summary + obd_context
 * - "Refusal expected" badge when expected_no_evidence is true
 * - Expected signal citations (table with sparkline per row,
 *   sourced from the Yamaha reference-stats sidecar JSON)
 * - Expected DTC citations
 * - Pitfall directives list
 * - Team review workflow (StarRating + ReviewSubmitForm +
 *   TeamFeedbackList) reused from the manual lane
 *
 * Author: Li-Ta Hsu
 */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowLeft, AlertTriangle, Loader2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ReviewSubmitForm } from "@/components/goldens/ReviewSubmitForm";
import { TeamFeedbackList } from "@/components/goldens/TeamFeedbackList";
import { getGolden, getYamahaReferenceStats } from "@/lib/api";
import type {
  ExpectedDTC,
  ExpectedSignalCitation,
  GoldenEntryDetail,
  YamahaReferenceStats,
} from "@/lib/types";

export default function ObdGoldenDetailPage() {
  const { t } = useTranslation();
  const params = useParams<{ id: string }>();
  const entryId = decodeURIComponent(params.id);

  const [entry, setEntry] = useState<GoldenEntryDetail | null>(null);
  const [refStats, setRefStats] =
    useState<YamahaReferenceStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      getGolden(entryId),
      // Best-effort: failure to load ref stats just disables the
      // sparkline, doesn't block the page.
      getYamahaReferenceStats().catch(() => null),
    ])
      .then(([entryRes, statsRes]) => {
        setEntry(entryRes);
        setRefStats(statsRes);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, [entryId]);

  if (loading) {
    return (
      <div className="container mx-auto px-4 py-6">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("goldens.detail.loading")}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container mx-auto px-4 py-6 space-y-3">
        <Link
          href="/goldens/obd"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          {t("goldens.detail.backToListing")}
        </Link>
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    );
  }

  if (!entry) return null;

  return (
    <div className="container mx-auto px-4 py-6 space-y-4">
      <Link
        href="/goldens/obd"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        {t("goldens.detail.backToListing")}
      </Link>

      {/* Question card */}
      <Card>
        <CardHeader className="space-y-2">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            <Badge variant="outline">{entry.question_type}</Badge>
            <Badge variant="outline">{entry.difficulty}</Badge>
            {entry.expected_no_evidence && (
              <Badge
                variant="default"
                className="gap-1 bg-amber-600 hover:bg-amber-700"
              >
                <AlertTriangle className="h-3 w-3" />
                {t("goldens.obdDetail.refusalExpected")}
              </Badge>
            )}
            {entry.is_locked && (
              <Badge variant="secondary">
                {t("goldens.obdDetail.locked")}
              </Badge>
            )}
            <code className="ml-auto text-xs text-muted-foreground">
              {entry.id}
            </code>
          </CardTitle>
          <p className="text-base font-medium">{entry.question_en}</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <h4 className="text-xs font-semibold uppercase text-muted-foreground">
              {t("goldens.obdDetail.goldenSummary")}
            </h4>
            <p className="mt-1 whitespace-pre-wrap text-sm">
              {entry.golden_summary_en}
            </p>
          </div>

          {entry.obd_context && (
            <div>
              <h4 className="text-xs font-semibold uppercase text-muted-foreground">
                {t("goldens.obdDetail.context")}
              </h4>
              <p className="mt-1 whitespace-pre-wrap text-sm">
                {entry.obd_context}
              </p>
            </div>
          )}

          {entry.expected_signal_citations.length > 0 && (
            <ExpectedSignalCitationsTable
              citations={entry.expected_signal_citations}
              refStats={refStats}
            />
          )}

          {entry.expected_dtcs.length > 0 && (
            <ExpectedDtcsTable dtcs={entry.expected_dtcs} />
          )}

          {entry.pitfall_directives.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase text-muted-foreground">
                {t("goldens.obdDetail.pitfallDirectives")}
              </h4>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
                {entry.pitfall_directives.map((p, i) => (
                  <li key={i} className="text-muted-foreground">
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Review submission */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {t("goldens.detail.submitReview")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ReviewSubmitForm
            entryId={entry.id}
            onSubmitted={() => setRefreshKey((k) => k + 1)}
          />
        </CardContent>
      </Card>

      {/* Team feedback history */}
      <TeamFeedbackList
        entryId={entry.id}
        refreshKey={refreshKey}
      />
    </div>
  );
}

interface ExpectedSignalCitationsTableProps {
  citations: ExpectedSignalCitation[];
  refStats: YamahaReferenceStats | null;
}

function ExpectedSignalCitationsTable({
  citations,
  refStats,
}: ExpectedSignalCitationsTableProps) {
  const { t } = useTranslation();
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase text-muted-foreground">
        {t("goldens.obdDetail.expectedSignalCitations")} (
        {citations.length})
      </h4>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b">
              <th className="px-2 py-1 text-left font-medium text-muted-foreground">
                {t("goldens.obdDetail.signal")}
              </th>
              <th className="px-2 py-1 text-left font-medium text-muted-foreground">
                {t("goldens.obdDetail.stat")}
              </th>
              <th className="px-2 py-1 text-right font-medium text-muted-foreground">
                {t("goldens.obdDetail.expectedValue")}
              </th>
              <th className="px-2 py-1 text-right font-medium text-muted-foreground">
                {t("goldens.obdDetail.tolerance")}
              </th>
              <th className="px-2 py-1 text-left font-medium text-muted-foreground">
                {t("goldens.obdDetail.fixtureRange")}
              </th>
            </tr>
          </thead>
          <tbody>
            {citations.map((c, i) => {
              const stats = refStats?.signal_stats[c.signal] ?? null;
              return (
                <tr key={i} className="border-b last:border-b-0">
                  <td className="px-2 py-1 font-mono text-xs">
                    {c.signal}
                  </td>
                  <td className="px-2 py-1 text-xs">
                    {c.stat ?? "—"}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums text-xs">
                    {c.value !== null && c.value !== undefined
                      ? c.value.toFixed(2)
                      : "—"}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums text-xs text-muted-foreground">
                    {c.value_tolerance_rel !== null &&
                    c.value_tolerance_rel !== undefined
                      ? `±${(c.value_tolerance_rel * 100).toFixed(0)}%`
                      : "—"}
                  </td>
                  <td className="px-2 py-1">
                    {stats ? (
                      <SignalSparkline stats={stats} />
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        —
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface ExpectedDtcsTableProps {
  dtcs: ExpectedDTC[];
}

function ExpectedDtcsTable({ dtcs }: ExpectedDtcsTableProps) {
  const { t } = useTranslation();
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase text-muted-foreground">
        {t("goldens.obdDetail.expectedDtcs")} ({dtcs.length})
      </h4>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b">
              <th className="px-2 py-1 text-left font-medium text-muted-foreground">
                {t("goldens.obdDetail.code")}
              </th>
              <th className="px-2 py-1 text-left font-medium text-muted-foreground">
                {t("goldens.obdDetail.status")}
              </th>
              <th className="px-2 py-1 text-left font-medium text-muted-foreground">
                {t("goldens.obdDetail.ecu")}
              </th>
            </tr>
          </thead>
          <tbody>
            {dtcs.map((d, i) => (
              <tr key={i} className="border-b last:border-b-0">
                <td className="px-2 py-1 font-mono text-xs">
                  {d.code}
                </td>
                <td className="px-2 py-1 text-xs">
                  {d.status ?? "—"}
                </td>
                <td className="px-2 py-1 text-xs">
                  {d.ecu ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * Tiny SVG sparkline (~50 lines, no library dep).
 *
 * Renders a 5-point profile (min, p50 ish, mean, p95 ish, max)
 * normalized to a 100x20 viewBox.  Not a real time-series chart
 * — for that we'd need the raw samples, which the sidecar
 * deliberately doesn't ship.  Instead we visualise the
 * distribution shape:
 *
 *   min ─── p50 ─── mean ─── p95 ─── max
 *
 * Useful for the reviewer to see at a glance "is this expected
 * value near the typical or at an extreme of the fixture?"
 */
function SignalSparkline({
  stats,
}: {
  stats: YamahaReferenceStats["signal_stats"][string];
}) {
  // Layout
  const width = 100;
  const height = 20;
  const padX = 4;
  const padY = 2;

  // Points in order: min, p50, mean, p95, max
  const points = [stats.min, stats.p50, stats.mean, stats.p95, stats.max];
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const range = hi - lo === 0 ? 1 : hi - lo;

  const xs = points.map(
    (_, i) =>
      padX + (i * (width - 2 * padX)) / (points.length - 1),
  );
  const ys = points.map(
    (v) =>
      height - padY - ((v - lo) / range) * (height - 2 * padY),
  );

  const path = points
    .map((_, i) => `${i === 0 ? "M" : "L"} ${xs[i]} ${ys[i]}`)
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-5 w-24"
      preserveAspectRatio="none"
      role="img"
      aria-label={`Range ${stats.min.toFixed(1)} to ${stats.max.toFixed(1)}`}
    >
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        className="text-blue-500"
      />
      {/* Mean marker */}
      <circle
        cx={xs[2]}
        cy={ys[2]}
        r={1.5}
        className="fill-blue-500"
      />
    </svg>
  );
}
