"use client";

/**
 * /goldens landing page (HARNESS-21 [2b/4]).
 *
 * Two-card layout: links to /goldens/manual (manual-eval lane)
 * and /goldens/obd (OBD-eval lane).  Each card shows the total
 * count + how many have been reviewed by the team.  Replaces the
 * previous single-lane listing which now lives at /goldens/manual.
 *
 * Auth-gated like all /goldens routes; redirects to /login when
 * unauthenticated (existing AuthProvider behavior).
 *
 * Author: Li-Ta Hsu
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowLeft, BookOpen, Cpu, Loader2 } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { listGoldens } from "@/lib/api";
import type { GoldenLane } from "@/lib/types";

interface LaneCounts {
  total: number;
  reviewed: number;
}

const _emptyCounts = (): LaneCounts => ({ total: 0, reviewed: 0 });

export default function GoldensLandingPage() {
  const { t } = useTranslation();
  const [manualCounts, setManualCounts] = useState<LaneCounts>(
    _emptyCounts(),
  );
  const [obdCounts, setObdCounts] = useState<LaneCounts>(
    _emptyCounts(),
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const fetchLane = async (
      lane: GoldenLane,
    ): Promise<LaneCounts> => {
      const res = await listGoldens({ lane, limit: 500 });
      const reviewed = res.items.filter(
        (it) =>
          it.latest_review_status !== null &&
          it.latest_review_status !== "draft",
      ).length;
      return { total: res.items.length, reviewed };
    };

    Promise.all([fetchLane("manual"), fetchLane("obd")])
      .then(([m, o]) => {
        if (cancelled) return;
        setManualCounts(m);
        setObdCounts(o);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="container mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center gap-2">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          {t("goldens.common.home")}
        </Link>
      </div>

      <Card>
        <CardHeader className="space-y-2">
          <CardTitle className="flex items-center gap-2">
            {t("goldens.landing.title")}
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            {t("goldens.landing.description")}
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("goldens.landing.loading")}
            </div>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {!loading && !error && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <LaneCard
                lane="manual"
                icon={<BookOpen className="h-5 w-5" />}
                title={t("goldens.landing.manualTitle")}
                description={t("goldens.landing.manualDescription")}
                counts={manualCounts}
                href="/goldens/manual"
              />
              <LaneCard
                lane="obd"
                icon={<Cpu className="h-5 w-5" />}
                title={t("goldens.landing.obdTitle")}
                description={t("goldens.landing.obdDescription")}
                counts={obdCounts}
                href="/goldens/obd"
              />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

interface LaneCardProps {
  lane: GoldenLane;
  icon: React.ReactNode;
  title: string;
  description: string;
  counts: LaneCounts;
  href: string;
}

function LaneCard({
  icon,
  title,
  description,
  counts,
  href,
}: LaneCardProps) {
  const { t } = useTranslation();
  return (
    <Link
      href={href}
      className="block transition hover:scale-[1.01] hover:shadow-md"
    >
      <Card className="h-full">
        <CardHeader className="space-y-2">
          <CardTitle className="flex items-center gap-2 text-base">
            {icon}
            {title}
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            {description}
          </p>
        </CardHeader>
        <CardContent>
          <div className="flex items-baseline gap-4">
            <div>
              <div className="text-3xl font-semibold tabular-nums">
                {counts.total}
              </div>
              <div className="text-xs text-muted-foreground">
                {t("goldens.landing.totalLabel")}
              </div>
            </div>
            <div>
              <div className="text-3xl font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                {counts.reviewed}
              </div>
              <div className="text-xs text-muted-foreground">
                {t("goldens.landing.reviewedLabel")}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
