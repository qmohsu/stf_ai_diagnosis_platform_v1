import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function severityColor(severity: string): string {
  switch (severity) {
    case "critical":
    case "high":
      return "text-red-600 bg-red-50 border-red-200";
    case "warning":
    case "medium":
      return "text-amber-600 bg-amber-50 border-amber-200";
    case "info":
    case "low":
      return "text-blue-600 bg-blue-50 border-blue-200";
    default:
      return "text-gray-600 bg-gray-50 border-gray-200";
  }
}

export function severityBadgeColor(severity: string): string {
  switch (severity) {
    case "critical":
    case "high":
      return "bg-red-100 text-red-800";
    case "warning":
    case "medium":
      return "bg-amber-100 text-amber-800";
    case "info":
    case "low":
      return "bg-blue-100 text-blue-800";
    default:
      return "bg-gray-100 text-gray-800";
  }
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  if (mins < 60) return `${mins}m ${secs}s`;
  const hrs = Math.floor(mins / 60);
  const remainMins = mins % 60;
  return `${hrs}h ${remainMins}m`;
}

export function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return iso;
  }
}

export function formatNumber(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined) return "N/A";
  if (!isFinite(value)) return "N/A";
  return value.toFixed(decimals);
}

export function groupSignalsByUnit(
  columnUnits: Record<string, string>,
): Record<string, string[]> {
  const groups: Record<string, string[]> = {};
  for (const [signal, unit] of Object.entries(columnUnits)) {
    if (!groups[unit]) groups[unit] = [];
    groups[unit].push(signal);
  }
  return groups;
}

export function signalDisplayName(
  signal: string,
  t?: (key: string) => string,
): string {
  if (t) {
    const key = `signals.${signal}`;
    const translated = t(key);
    if (translated !== key) return translated;
  }
  return signal
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
