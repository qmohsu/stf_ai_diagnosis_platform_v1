"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  BookOpen,
  Box,
  CalendarDays,
  ChevronRight,
  CircleAlert,
  Database,
  FileText,
  Gauge,
  GitCommitHorizontal,
  History,
  Settings,
  ShieldAlert,
  TrendingUp,
  Wrench,
} from "lucide-react";
import { CoverageView } from "@/components/coverage-view";
import { DASH, Field, HealthTitle, Kpi } from "@/components/health-dashboard-ui";
import { IntervalView } from "@/components/interval-view";
import { useLocale } from "@/components/locale-provider";
import { OfflineBanner } from "@/components/offline-banner";
import { PortalApiError, portalFetch } from "@/lib/api-client";
import type {
  ComponentDetail,
  ComponentInstance,
  CoverageGap,
  CoverageWindow,
  DataState,
  IdentityType,
  PortalEvent,
} from "@/lib/domain";

export function ComponentWorkspace({ identityType, identity, componentCode }: { identityType: IdentityType; identity: string; componentCode: string }) {
  const { messages, locale } = useLocale();
  const params = useSearchParams();
  const query = useQuery({
    queryKey: ["component", identityType, identity, componentCode],
    queryFn: () => portalFetch<ComponentDetail>(`/api/portal/vehicles/${identityType}/${encodeURIComponent(identity)}/components/${encodeURIComponent(componentCode)}`),
  });

  if (query.isLoading) return <main className="loading-page">{messages.common.loading}</main>;
  if (query.isError || !query.data) {
    return <main className="error-page"><AlertTriangle /><h1>{messages.common.error}</h1><p>{query.error?.message}{query.error instanceof PortalApiError && query.error.errorId ? ` · ${messages.common.errorId}: ${query.error.errorId}` : ""}</p><button className="button" onClick={() => void query.refetch()}>{messages.common.retry}</button></main>;
  }

  const detail = query.data.data;
  const returnScope = params.get("returnScope");
  const back = `${detail.vehicle.canonicalPath}${returnScope ? `?scope=${encodeURIComponent(returnScope)}` : ""}`;
  const events = stateValue(detail.events);
  const orders = stateValue(detail.orders);
  const instances = stateValue(detail.instances);
  const transitions = stateValue(detail.transitions);
  const coverage = detail.coverage.kind === "ready" ? detail.coverage.value : { windows: [], gaps: [] };
  const latestFault = latestEvent(events.filter((event) => event.type === "fault"));
  const currentStatus = latestInstanceStatus(instances);
  const latestData = latestDate([
    ...events.flatMap((event) => [event.occurredAt, event.occurredAtUpperBound]),
    ...orders.map((order) => order.submittedAt),
    ...instances.flatMap((instance) => [instance.installedAt, instance.removedAt]),
    ...transitions.flatMap((transition) => [transition.occurredAt, transition.occurredAtUpperBound]),
    ...coverage.windows.map((window) => window.endDate),
  ]);
  const coverageRange = dateRange(coverage.windows, locale);

  return <main className="component-page component-health-page">
    {query.data.offline && <OfflineBanner cachedAt={query.data.cachedAt} />}
    <div className="crumb"><Link href={back}><ArrowLeft />{messages.vehicle.overview}</Link><ChevronRight /><span>{detail.component.nameZh}</span></div>

    <section className="health-overview" aria-labelledby="component-title">
      <div className="health-identity">
        <span className="health-component-icon"><Settings aria-hidden="true" /></span>
        <div><span className="eyebrow">{componentCode}</span><h1 id="component-title">{detail.component.nameZh}</h1><p>{detail.ancestry.map((node) => node.nameZh).join(" / ") || detail.component.nameZh}</p></div>
      </div>
      <div className="health-state" data-testid="component-status"><span>{messages.component.currentStatus}</span><strong>{currentStatus}</strong><small>{messages.component.lastUpdated}: {displayDate(latestData, locale)}</small></div>
      <div className="health-kpis">
        <Kpi tone="danger" icon={<ShieldAlert />} label={messages.component.currentFaults} value={DASH} />
        <Kpi tone="warning" icon={<TrendingUp />} label={messages.component.highRisk} value={DASH} />
        <Kpi tone="info" icon={<Wrench />} label={messages.component.maintenanceRecords} value={String(detail.summary.orders ?? orders.length)} />
        <Kpi tone="cyan" icon={<BarChart3 />} label={messages.component.dataCoverage} value={DASH} />
      </div>
    </section>

    <div className="health-dashboard">
      <section className="health-panel fault-panel" data-testid="current-fault">
        <HealthTitle tone="danger" icon={<ShieldAlert />} text={messages.component.currentFault} />
        <p className="health-disclaimer">{messages.component.currentFaultNote}</p>
        <div className="health-record-card">
          <div className="health-record-heading"><strong>{messages.component.latestHistoricalFault}</strong><span className="status-chip neutral">{latestFault ? messages.component.historicalBadge : DASH}</span></div>
          <div className="fault-fields">
            <Field label={messages.component.severity} value={DASH} />
            <Field label={messages.component.faultCode} value={latestFault?.failureMode ?? DASH} />
            <Field label={messages.component.occurredAt} value={displayDate(latestFault?.occurredAt ?? latestFault?.occurredAtUpperBound, locale)} />
            <Field wide label={messages.component.diagnosis} value={latestFault?.descriptionZh ?? DASH} />
          </div>
        </div>
        <div className="manual-block" data-testid="manual-match">
          <div className="manual-icon"><FileText aria-hidden="true" /></div>
          <div className="manual-copy"><h3>{messages.component.manualMatch}</h3><Field label={messages.component.boundManual} value={DASH} /><Field label={messages.component.manualPage} value={DASH} /><Field label={messages.component.chapter} value={DASH} /><Field label={messages.component.excerpt} value={DASH} /></div>
          <button type="button" className="button manual-button" disabled><BookOpen />{messages.component.openManual} · {DASH}</button>
        </div>
      </section>

      <section className="health-panel risk-panel" data-testid="risk-forecast">
        <HealthTitle tone="warning" icon={<TrendingUp />} text={messages.component.forecast} />
        <p className="health-disclaimer">{messages.component.forecastNote}</p>
        <div className="risk-layout">
          <div className="risk-facts">
            <Field label={messages.component.riskLevel} value={DASH} />
            <Field label={messages.component.probability} value={DASH} />
            <Field label={messages.component.predictionWindow} value={DASH} />
            <Field label={messages.component.trend} value={DASH} />
            <Field label={messages.component.confidence} value={DASH} />
          </div>
          <div className="risk-chart" aria-label={`${messages.component.riskTrend}: ${DASH}`}><strong>{DASH}</strong><div className="risk-grid" aria-hidden="true"><i /><i /><i /><i /></div><span>{messages.component.riskTrend}</span></div>
        </div>
        <div className="risk-factors"><h3>{messages.component.factors}</h3>{[1, 2, 3].map((item) => <div key={item}><span>{item}</span><strong>{DASH}</strong></div>)}</div>
      </section>

      <section className="health-panel maintenance-panel" data-testid="maintenance-history">
        <HealthTitle tone="info" icon={<History />} text={messages.component.maintenanceHistory} />
        <div className="health-table-wrap" tabIndex={0} aria-label={messages.component.maintenanceHistory}><table className="health-table"><thead><tr><th>{messages.component.date}</th><th>{messages.component.orderNumber}</th><th>{messages.component.maintenanceContent}</th><th>{messages.component.mileage}</th><th>{messages.component.recordStatus}</th></tr></thead><tbody>{orders.length ? orders.slice(0, 6).map((order) => <tr key={order.orderNo}><td>{displayDate(order.submittedAt, locale)}</td><td>{order.orderNo}</td><td>{order.orderTypeNameZh ?? order.orderTypeCode ?? order.remarks ?? DASH}</td><td>{order.submitMileage === null ? DASH : `${order.submitMileage.toLocaleString(locale)} km`}</td><td><span className="status-chip success">{order.status ?? DASH}</span></td></tr>) : <tr>{[0, 1, 2, 3, 4].map((cell) => <td key={cell}>{DASH}</td>)}</tr>}</tbody></table></div>
      </section>

      <section className="health-panel data-panel" data-testid="data-status">
        <HealthTitle tone="cyan" icon={<Database />} text={messages.component.dataStatus} />
        <div className="data-metrics">
          <Field label={messages.component.latestData} value={displayDate(latestData, locale)} />
          <Field label={messages.component.coverageRange} value={coverageRange} />
          <Field label={messages.component.completeness} value={DASH} />
          <Field label={messages.component.identifiedGaps} value={detail.coverage.kind === "ready" ? String(coverage.gaps.length) : DASH} />
          <Field label={messages.component.predictionStatus} value={DASH} />
        </div>
        <h3 className="timeline-title">{messages.component.coverageTimeline}</h3>
        <CoverageTimeline windows={coverage.windows} gaps={coverage.gaps} locale={locale} emptyLabel={DASH} />
        <div className="timeline-legend"><span><i className="valid" />{messages.component.validData}</span><span><i className="gap" />{messages.component.dataGap}</span></div>
      </section>
    </div>

    <details className="component-more">
      <summary><Database aria-hidden="true" />{messages.component.moreData}</summary>
      <div className="detail-grid supplemental-grid">
        <section className="panel wide"><Title icon={<CalendarDays />} text={messages.component.events} /><DetailStateView state={detail.events} render={(items) => <div className="event-list compact">{items.map((event) => { const occurred = event.occurredAt ?? event.occurredAtUpperBound; return <article key={event.id}><div className={`event-mark ${event.type}`} /><div><div className="event-head"><strong>{event.descriptionZh ?? event.type}</strong><span>{displayDate(occurred, locale)}</span></div><p>{event.type} · {event.orderNo ?? DASH} · {event.odometer ?? event.odometerUpperBound ?? DASH} km</p></div></article>; })}</div>} /></section>
        <section className="panel"><Title icon={<Box />} text={messages.component.parts} /><DetailStateView state={detail.parts} render={(items) => <div className="record-list">{items.map((part) => <article key={part.id}><strong>{part.partName ?? part.partNo ?? part.partId ?? DASH}</strong><span>{part.partNo ?? DASH}</span><small>{messages.component.quantity}: {part.quantity ?? DASH}</small></article>)}</div>} /></section>
        <section className="panel wide"><Title icon={<GitCommitHorizontal />} text={messages.component.instances} /><DetailStateView state={detail.instances} render={(items) => <div className="table-wrap" tabIndex={0} aria-label={messages.component.instances}><table><thead><tr><th>{messages.component.generation}</th><th>{messages.component.state}</th><th>{messages.component.installed}</th><th>{messages.component.removed}</th><th>{messages.component.quantity}</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td>{item.generation ?? DASH}</td><td>{item.currentState ?? DASH}</td><td>{item.installedAt ?? DASH}<small>{item.installedMileage === null ? "" : `${item.installedMileage} km`}</small></td><td>{item.removedAt ?? DASH}<small>{item.removedMileage === null ? "" : `${item.removedMileage} km`}</small></td><td>{item.quantity ?? DASH}</td></tr>)}</tbody></table></div>} /></section>
        <section className="panel"><Title icon={<GitCommitHorizontal />} text={messages.component.transitions} /><DetailStateView state={detail.transitions} render={(items) => <div className="record-list">{items.map((item) => <article key={item.id}><strong>{item.transitionType ?? (item.fromState || item.toState ? `${item.fromState ?? DASH} → ${item.toState ?? DASH}` : DASH)}</strong><span>{item.reason ?? item.eventType ?? DASH}</span><small>{item.occurredAt ?? item.occurredAtUpperBound ?? DASH}{item.odometer === null ? "" : ` · ${item.odometer} km`}</small></article>)}</div>} /></section>
        <section className="panel"><Title icon={<Gauge />} text={messages.component.intervals} /><DetailStateView state={detail.intervals} render={(items) => <IntervalView time={items.time} mileage={items.mileage} />} /></section>
        <section className="panel"><Title icon={<Gauge />} text={messages.component.coverage} /><DetailStateView state={detail.coverage} render={(item) => <CoverageView windows={item.windows} gaps={item.gaps} />} /></section>
        <section className="panel"><Title icon={<CircleAlert />} text={messages.component.quality} /><DetailStateView state={detail.quality} render={(items) => <div className="record-list">{items.map((item) => <article key={item.id}><strong>{item.issueType ?? DASH}</strong><span>{item.message ?? DASH}</span><small>{messages.component.severity}: {item.severity ?? DASH}</small></article>)}</div>} /></section>
      </div>
    </details>
  </main>;
}

function Title({ icon, text }: { icon: React.ReactNode; text: string }) {
  return <div className="panel-title"><span>{icon}</span><h2>{text}</h2></div>;
}

function DetailStateView<T>({ state, render }: { state: DataState<T>; render: (value: T) => React.ReactNode }) {
  return state.kind === "ready" ? <>{render(state.value)}</> : <span className="detail-dash">{DASH}</span>;
}

function stateValue<T>(state: { kind: string; value?: T[] }): T[] {
  return state.kind === "ready" && state.value ? state.value : [];
}

function dateValue(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

function latestDate(values: Array<string | null | undefined>): string | null {
  let selected: string | null = null;
  let selectedTime = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    const time = dateValue(value);
    if (value && time !== null && time > selectedTime) { selected = value; selectedTime = time; }
  }
  return selected;
}

function latestEvent(events: PortalEvent[]): PortalEvent | null {
  return events.reduce<PortalEvent | null>((selected, event) => {
    if (!selected) return event;
    const eventTime = dateValue(event.occurredAt ?? event.occurredAtUpperBound) ?? Number.NEGATIVE_INFINITY;
    const selectedTime = dateValue(selected.occurredAt ?? selected.occurredAtUpperBound) ?? Number.NEGATIVE_INFINITY;
    return eventTime >= selectedTime ? event : selected;
  }, null);
}

function latestInstanceStatus(instances: ComponentInstance[]): string {
  if (!instances.length) return DASH;
  const latest = [...instances].sort((a, b) => (a.generation ?? 0) - (b.generation ?? 0)).at(-1);
  return latest?.currentState ?? DASH;
}

function displayDate(value: string | null | undefined, locale: string): string {
  if (!value) return DASH;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(locale);
}

function dateRange(windows: CoverageWindow[], locale: string): string {
  const starts = windows.map((window) => window.startDate).filter((value): value is string => dateValue(value) !== null);
  const ends = windows.map((window) => window.endDate).filter((value): value is string => dateValue(value) !== null);
  const start = starts.sort((a, b) => (dateValue(a) ?? 0) - (dateValue(b) ?? 0))[0];
  const end = ends.sort((a, b) => (dateValue(b) ?? 0) - (dateValue(a) ?? 0))[0];
  return start && end ? `${displayDate(start, locale)} – ${displayDate(end, locale)}` : DASH;
}

function CoverageTimeline({ windows, gaps, locale, emptyLabel }: { windows: CoverageWindow[]; gaps: CoverageGap[]; locale: string; emptyLabel: string }) {
  const ranges = [
    ...windows.map((item) => ({ kind: "valid", start: item.startDate, end: item.endDate })),
    ...gaps.map((item) => ({ kind: "gap", start: item.startDate, end: item.endDate })),
  ].map((item) => ({ ...item, startMs: dateValue(item.start), endMs: dateValue(item.end) })).filter((item): item is typeof item & { startMs: number; endMs: number } => item.startMs !== null && item.endMs !== null);
  if (!ranges.length) return <div className="timeline-empty">{emptyLabel}</div>;
  const min = Math.min(...ranges.map((range) => range.startMs));
  const max = Math.max(...ranges.map((range) => range.endMs));
  const span = Math.max(1, max - min);
  return <div className="coverage-timeline"><div className="coverage-track">{ranges.map((range, index) => <i key={`${range.kind}-${index}`} className={range.kind} title={`${displayDate(range.start, locale)} – ${displayDate(range.end, locale)}`} style={{ left: `${((range.startMs - min) / span) * 100}%`, width: `${Math.max(1.5, ((range.endMs - range.startMs) / span) * 100)}%` }} />)}</div><div><span>{new Date(min).toLocaleDateString(locale)}</span><span>{new Date(max).toLocaleDateString(locale)}</span></div></div>;
}
