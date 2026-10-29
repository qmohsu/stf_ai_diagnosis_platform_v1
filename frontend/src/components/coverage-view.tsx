"use client";
import { useLocale } from "@/components/locale-provider";
import type { CoverageGap, CoverageWindow } from "@/lib/domain";
export function CoverageView({ windows, gaps }: { windows: CoverageWindow[]; gaps: CoverageGap[] }) {
  const { messages, locale } = useLocale();
  return <div className="coverage-view"><div className="metric-grid"><div className="metric"><strong>{windows.length}</strong><span>{messages.vehicle.windows}</span></div><div className="metric"><strong>{gaps.length}</strong><span>{messages.vehicle.gaps}</span></div></div><div className="coverage-records">{windows.slice(0, 3).map((item) => <article key={item.id}><strong>{item.coverageType ?? item.coverageStatus ?? messages.vehicle.windows}</strong><span>{date(item.startDate, locale)} → {date(item.endDate, locale)}</span><small>{item.startMileage ?? "-"} → {item.endMileage ?? "-"} km · {item.confidence ?? "-"}</small>{item.note && <p>{item.note}</p>}</article>)}{gaps.slice(0, 3).map((item) => <article className="gap" key={item.id}><strong>{item.gapType ?? messages.vehicle.gaps}</strong><span>{date(item.startDate, locale)} → {date(item.endDate, locale)}</span><small>{item.gapDays ?? "-"} {messages.common.days} · {item.confidence ?? "-"}</small>{item.description && <p>{item.description}</p>}</article>)}</div></div>;
}
function date(value: string | null, locale: string): string { if (!value) return "-"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(locale); }
