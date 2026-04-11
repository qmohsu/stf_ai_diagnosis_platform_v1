"use client";

import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";

interface IterationProgressProps {
  currentIteration: number;
  maxIterations: number | null;
  autonomyTier: number | null;
  autonomyStrategy: string | null;
  streaming: boolean;
}

const TIER_COLORS: Record<number, string> = {
  0: "bg-gray-100 text-gray-700 border-gray-300",
  1: "bg-blue-100 text-blue-700 border-blue-300",
  2: "bg-amber-100 text-amber-700 border-amber-300",
  3: "bg-purple-100 text-purple-700 border-purple-300",
};

export function IterationProgress({
  currentIteration,
  maxIterations,
  autonomyTier,
  autonomyStrategy,
  streaming,
}: IterationProgressProps) {
  const { t } = useTranslation();

  const tierKey = String(autonomyTier ?? 0) as "0" | "1" | "2" | "3";
  const tierLabel = autonomyTier !== null
    ? t(`agent.tierLabel.${tierKey}`)
    : null;
  const tierColor = TIER_COLORS[autonomyTier ?? 0] ?? TIER_COLORS[0];

  const progress = maxIterations && maxIterations > 0
    ? Math.min((currentIteration / maxIterations) * 100, 100)
    : 0;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        {/* Iteration counter */}
        <span className={`text-sm font-medium ${streaming ? "animate-pulse" : ""}`}>
          {maxIterations
            ? t("agent.iterationOf", { current: currentIteration, max: maxIterations })
            : currentIteration > 0
              ? t("agent.iteration", { current: currentIteration })
              : null}
        </span>

        {/* Autonomy tier badge */}
        {autonomyTier !== null && (
          <div className="flex items-center gap-2">
            <Badge className={tierColor}>
              {t("agent.tier", { tier: autonomyTier })}
              {tierLabel ? ` \u2014 ${tierLabel}` : ""}
            </Badge>
            {autonomyStrategy && (
              <span className="text-xs text-muted-foreground">
                {t("agent.strategy", { strategy: autonomyStrategy })}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Progress bar */}
      {streaming && maxIterations && maxIterations > 0 && (
        <div className="w-full h-1 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full bg-primary transition-all duration-500 ease-out rounded-full"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </div>
  );
}
