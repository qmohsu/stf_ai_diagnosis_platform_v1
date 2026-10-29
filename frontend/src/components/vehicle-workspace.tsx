"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BarChart3, ChevronRight, Clock3, Database, Gauge, Menu, ShieldAlert, TrendingUp } from "lucide-react";
import { BomTree } from "@/components/bom-tree";
import { CoverageView } from "@/components/coverage-view";
import { DataStateView } from "@/components/data-state-view";
import { DASH, HealthTitle, Kpi } from "@/components/health-dashboard-ui";
import { IntervalView } from "@/components/interval-view";
import { useLocale } from "@/components/locale-provider";
import { OfflineBanner } from "@/components/offline-banner";
import { Sheet } from "@/components/ui/sheet";
import { PortalApiError, portalFetch } from "@/lib/api-client";
import type { DataState, IdentityType, PortalEvent, VehicleHome } from "@/lib/domain";
import { formatLatestVehicleData, latestVehicleData } from "@/lib/vehicle-dashboard";

export function VehicleWorkspace({ identityType, identity }: { identityType: IdentityType; identity: string }) {
  const { messages, locale } = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [sheetOpen, setSheetOpen] = useState(false);
  const scope = params.get("scope");
  const query = useQuery({ queryKey: ["vehicle-home", identityType, identity], queryFn: () => portalFetch<VehicleHome>(`/api/portal/vehicles/${identityType}/${encodeURIComponent(identity)}/home`) });
  const home = query.data?.data;

  useEffect(() => {
    if (home && pathname !== home.vehicle.canonicalPath) router.replace(`${home.vehicle.canonicalPath}${scope ? `?scope=${encodeURIComponent(scope)}` : ""}`);
  }, [home, pathname, router, scope]);

  const setScope = (next: string | null) => {
    const vehiclePath = home?.vehicle.canonicalPath ?? pathname;
    router.replace(next ? `${vehiclePath}?scope=${encodeURIComponent(next)}` : vehiclePath, { scroll: false });
    setSheetOpen(false);
  };
  const selectedNode = home?.bom.kind === "ready" ? home.bom.value.find((node) => node.code === scope) ?? null : null;
  const scopedEvents = useMemo<DataState<PortalEvent[]> | null>(() => {
    if (!home || home.events.kind !== "ready" || !scope) return null;
    const events = home.events.value.filter((event) => event.componentCode === scope || event.systemCode === scope);
    return events.length ? { kind: "ready", value: events } : { kind: "empty" };
  }, [home, scope]);

  if (query.isLoading) return <main className="loading-page">{messages.common.loading}</main>;
  if (query.isError || !home) return <main className="error-page"><AlertTriangle /><h1>{messages.common.error}</h1><p>{query.error?.message}{query.error instanceof PortalApiError && query.error.errorId ? ` · ${messages.common.errorId}: ${query.error.errorId}` : ""}</p><button className="button" onClick={() => void query.refetch()}>{messages.common.retry}</button></main>;

  const tree = home.bom.kind === "ready" ? <BomTree nodes={home.bom.value} selected={scope} onSelect={setScope} /> : <DataStateView state={home.bom} render={() => null} onRetry={() => void query.refetch()} />;
  const latest = formatLatestVehicleData(latestVehicleData(home), locale);
  const sectionErrors = [home.events, home.coverage, home.readiness, home.survivalIntervals].filter((state) => state.kind === "error");

  return <main className="vehicle-page vehicle-dashboard-page">
    <h1 className="sr-only">{home.vehicle.manufacturer} {home.vehicle.model} · {messages.dashboard.summary}</h1>
    {query.data?.offline && <OfflineBanner cachedAt={query.data.cachedAt} />}
    <button className="tree-mobile-trigger button secondary vehicle-system-trigger" onClick={() => setSheetOpen(true)}><Menu />{messages.dashboard.wholeSystem}</button>
    <Sheet open={sheetOpen} onOpenChange={setSheetOpen} title={messages.dashboard.wholeSystem}>{tree}</Sheet>
    <div className="vehicle-mobile-identity"><strong>{home.vehicle.manufacturer} {home.vehicle.model}</strong><span>VIN {home.vehicle.vin ?? DASH}</span>{!home.vehicle.vin && <small>{messages.vehicle.noVin}</small>}</div>

    <div className="vehicle-dashboard-shell">
      <aside className="vehicle-system-sidebar">
        <div className="vehicle-system-title"><strong>{messages.dashboard.wholeSystem}</strong><small>{home.bom.kind === "ready" ? home.bom.value.length : DASH}</small></div>
        <div className="vehicle-sidebar-identity"><strong>{home.vehicle.manufacturer} {home.vehicle.model}</strong><span>VIN {home.vehicle.vin ?? DASH}</span>{!home.vehicle.vin && <small>{messages.vehicle.noVin}</small>}</div>
        {tree}
      </aside>

      <div className="vehicle-dashboard-content">
        <section className="vehicle-dashboard-kpis" aria-label={messages.dashboard.summary}>
          <Kpi tone="danger" icon={<ShieldAlert />} label={messages.dashboard.currentFaults} value={DASH} testId="vehicle-current-fault-kpi" />
          <Kpi tone="warning" icon={<TrendingUp />} label={messages.dashboard.highRisk} value={DASH} testId="vehicle-risk-kpi" />
          <Kpi tone="info" icon={<Clock3 />} label={messages.dashboard.latestData} value={latest} testId="vehicle-latest-data-kpi" />
          <Kpi tone="cyan" icon={<BarChart3 />} label={messages.dashboard.coverageRate} value={DASH} testId="vehicle-coverage-kpi" />
        </section>

        {sectionErrors.length > 0 && <div className="dashboard-section-error" role="alert"><AlertTriangle /><span>{messages.dashboard.sectionErrors} · {sectionErrors.map((state) => state.kind === "error" ? state.errorId : "").join(" · ")}</span><button type="button" className="button secondary" onClick={() => void query.refetch()}>{messages.common.retry}</button></div>}

        {selectedNode && <section className="scope-bar dashboard-scope"><div><small>{messages.dashboard.selectedScope}</small><strong>{selectedNode.nameZh}</strong><span>{selectedNode.code}</span></div><Link className="button" href={`${home.vehicle.canonicalPath}/components/${encodeURIComponent(selectedNode.code)}?returnScope=${encodeURIComponent(selectedNode.code)}`}>{messages.dashboard.viewDetails}<ChevronRight /></Link></section>}

        <div className="vehicle-dashboard-primary">
          <section className="health-panel vehicle-status-panel" data-testid="vehicle-current-faults">
            <HealthTitle tone="danger" icon={<ShieldAlert />} text={`${messages.dashboard.currentFault} (${DASH})`} />
            <p className="health-disclaimer">{messages.dashboard.currentFaultNote}</p>
            <DashboardTable label={messages.dashboard.currentFault} headers={[messages.dashboard.faultData, messages.dashboard.severity, messages.dashboard.occurredAt, messages.dashboard.diagnosisSummary, messages.dashboard.confidence, messages.dashboard.action]} />
          </section>

          <section className="health-panel vehicle-status-panel" data-testid="vehicle-risk-forecast">
            <HealthTitle tone="warning" icon={<TrendingUp />} text={`${messages.dashboard.riskTitle} (${DASH})`} />
            <p className="health-disclaimer">{messages.dashboard.riskNote}</p>
            <DashboardTable label={messages.dashboard.riskTitle} headers={[messages.dashboard.riskData, messages.dashboard.riskLevel, messages.dashboard.probability, messages.dashboard.predictionWindow, messages.dashboard.trend, messages.dashboard.factors, messages.dashboard.action]} />
          </section>
        </div>

        <details className="vehicle-more-data">
          <summary><Database aria-hidden="true" />{messages.dashboard.moreData}</summary>
          <div className="vehicle-supplemental-grid">
            <section className="panel supplemental-events"><PanelTitle icon={<Clock3 />} title={messages.vehicle.recentEvents} note={messages.vehicle.historical} /><DashboardStateView state={scopedEvents ?? home.events} onRetry={() => void query.refetch()} render={(events) => <EventList events={events.slice(0, 12)} locale={locale} />} /></section>
            <section className="panel"><PanelTitle icon={<Database />} title={messages.vehicle.coverage} /><DashboardStateView state={home.coverage} onRetry={() => void query.refetch()} render={(coverage) => <CoverageView windows={coverage.windows} gaps={coverage.gaps} />} /></section>
            <section className="panel"><PanelTitle icon={<Gauge />} title={messages.vehicle.readiness} /><DashboardStateView state={home.readiness} onRetry={() => void query.refetch()} render={(item) => <div className="metric-grid"><Metric label={messages.vehicle.instances} value={item.instances} /><Metric label={messages.vehicle.observedFailures} value={item.observedFailures} /><Metric label={messages.vehicle.leftTruncated} value={item.leftTruncated} /><Metric label={messages.vehicle.excluded} value={item.excluded} /></div>} /></section>
            <section className="panel supplemental-intervals"><PanelTitle icon={<Gauge />} title={messages.vehicle.intervals} /><DashboardStateView state={home.survivalIntervals} onRetry={() => void query.refetch()} render={(item) => <IntervalView time={item.time} mileage={item.mileage} />} /></section>
            <section className="v3-boundary supplemental-v3"><div><span>{messages.vehicle.v3Boundary}</span><h2>{messages.common.deferred}</h2><p>{messages.vehicle.v3Note}</p></div><div className="boundary-items"><span>logs —</span><span>diagnosis —</span><span>report —</span><span>manuals —</span></div></section>
          </div>
        </details>
      </div>
    </div>
  </main>;
}

function DashboardTable({ label, headers }: { label: string; headers: string[] }) {
  return <div className="vehicle-health-table-wrap" tabIndex={0}><table className="vehicle-health-table"><caption className="sr-only">{label}</caption><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody><tr>{headers.map((header) => <td key={header}>{DASH}</td>)}</tr></tbody></table></div>;
}

function DashboardStateView<T>({ state, render, onRetry }: { state: DataState<T>; render: (value: T) => React.ReactNode; onRetry: () => void }) {
  const { messages } = useLocale();
  if (state.kind === "ready") return <>{render(state.value)}</>;
  if (state.kind === "error") return <div className="state-error" role="alert"><span>{state.message} ({messages.common.errorId}: {state.errorId})</span><button type="button" className="button secondary" onClick={onRetry}>{messages.common.retry}</button></div>;
  return <span className="detail-dash" aria-label={state.kind === "pendingSource" ? messages.common.pending : undefined}>{DASH}</span>;
}

function PanelTitle({ icon, title, note }: { icon: React.ReactNode; title: string; note?: string }) { return <div className="panel-title"><span>{icon}</span><div><h2>{title}</h2>{note && <p>{note}</p>}</div></div>; }
function Metric({ label, value }: { label: string; value: number }) { return <div className="metric"><strong>{value}</strong><span>{label}</span></div>; }
function EventList({ events, locale }: { events: PortalEvent[]; locale: string }) { return <div className="event-list">{events.map((event) => { const occurred = event.occurredAt ?? event.occurredAtUpperBound; return <article key={event.id}><div className={`event-mark ${event.type}`} /><div><div className="event-head"><strong>{event.descriptionZh ?? event.subtype ?? event.type}</strong><span>{occurred ? new Date(occurred).toLocaleDateString(locale) : DASH}</span></div><p>{event.type}{event.componentCode ? ` · ${event.componentCode}` : ""}{event.orderNo ? ` · ${event.orderNo}` : ""}</p></div></article>; })}</div>; }
