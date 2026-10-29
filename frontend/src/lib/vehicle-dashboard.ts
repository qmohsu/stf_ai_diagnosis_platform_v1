import type { VehicleHome } from "@/lib/domain";

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const RELATIVE_LIMIT_MS = 7 * 24 * 60 * 60 * 1_000;

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

export function latestVehicleData(home: Pick<VehicleHome, "events" | "coverage" | "survivalIntervals">): string | null {
  const candidates: string[] = [];
  if (home.events.kind === "ready") {
    for (const event of home.events.value) {
      if (event.occurredAt) candidates.push(event.occurredAt);
      if (event.occurredAtUpperBound) candidates.push(event.occurredAtUpperBound);
    }
  }
  if (home.coverage.kind === "ready") {
    for (const window of home.coverage.value.windows) if (window.endDate) candidates.push(window.endDate);
  }
  if (home.survivalIntervals.kind === "ready") {
    for (const interval of home.survivalIntervals.value.time) if (typeof interval.exit === "string") candidates.push(interval.exit);
  }

  let latest: string | null = null;
  let latestTime = Number.NEGATIVE_INFINITY;
  for (const candidate of candidates) {
    const time = timestamp(candidate);
    if (time !== null && time > latestTime) {
      latest = candidate;
      latestTime = time;
    }
  }
  return latest;
}

export function formatLatestVehicleData(value: string | null, locale: string, now = Date.now()): string {
  if (!value) return "-";
  const time = timestamp(value);
  if (time === null) return "-";
  const date = new Date(time);
  if (DATE_ONLY.test(value)) return date.toLocaleDateString(locale);

  const elapsed = now - time;
  if (elapsed < 0 || elapsed >= RELATIVE_LIMIT_MS) return date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
  if (elapsed < 60_000) return locale === "zh-CN" ? "刚刚" : "剛剛";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}${locale === "zh-CN" ? "分钟前" : "分鐘前"}`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}${locale === "zh-CN" ? "小时前" : "小時前"}`;
  return `${Math.floor(elapsed / 86_400_000)}${locale === "zh-CN" ? "天前" : "日前"}`;
}
