"use client";
import { useLocale } from "@/components/locale-provider";
import type { SurvivalInterval } from "@/lib/domain";
export function IntervalView({ time, mileage }: { time: SurvivalInterval[]; mileage: SurvivalInterval[] }) {
  const { messages } = useLocale();
  return <div className="interval-view"><div className="metric-grid"><div className="metric"><strong>{time.length}</strong><span>{messages.vehicle.timeAxis}</span></div><div className="metric"><strong>{mileage.length}</strong><span>{messages.vehicle.mileageAxis}</span></div></div><div className="interval-records">{[...time.slice(0, 2), ...mileage.slice(0, 2)].map((item, index) => <article key={`${item.componentCode}-${item.episode}-${index}`}><div><strong>{item.componentCode ?? "-"}</strong><span>#{item.episode ?? "-"}</span></div><p>{item.entry ?? "-"} → {item.exit ?? "-"}</p><small>{item.eventObserved === null ? "-" : item.eventObserved ? messages.vehicle.observedEvent : messages.vehicle.censored} · {item.inclusionStatus ?? "-"} · {item.truncationType ?? "-"}</small></article>)}</div></div>;
}
