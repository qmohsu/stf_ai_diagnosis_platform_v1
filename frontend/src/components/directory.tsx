"use client";
import { useDeferredValue, useState } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, BusFront, Database, Gauge, Search, Wrench } from "lucide-react";
import { useLocale } from "@/components/locale-provider";
import { OfflineBanner } from "@/components/offline-banner";
import { PortalApiError, portalFetch } from "@/lib/api-client";
import type { VehicleDirectoryResponse } from "@/lib/domain";

export function Directory() {
  const { messages } = useLocale(); const [search, setSearch] = useState(""); const deferred = useDeferredValue(search);
  const query = useQuery({ queryKey: ["vehicles", deferred], queryFn: () => portalFetch<VehicleDirectoryResponse>(`/api/portal/vehicles?q=${encodeURIComponent(deferred)}`), placeholderData: keepPreviousData });
  const data = query.data?.data;
  return <main className="directory-page">
    {query.data?.offline && <OfflineBanner cachedAt={query.data.cachedAt} />}
    <section className="hero"><div><span className="eyebrow">{messages.directory.eyebrow}</span><h1>{messages.directory.title}</h1><p>{messages.directory.subtitle}</p></div><div className="hero-orbit" aria-hidden="true"><BusFront /><span /><span /></div></section>
    <section className="directory-panel" aria-labelledby="directory-title">
      <div className="directory-tools"><div><h2 id="directory-title">{messages.directory.result}</h2>{data && <p>{data.filtered} / {data.total}</p>}</div><label className="search-box"><Search aria-hidden="true" /><span className="sr-only">{messages.common.search}</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={messages.directory.placeholder} /></label></div>
      {data && <div className="stat-strip"><Stat icon={<BusFront />} value={data.filtered} label={messages.directory.allVehicles} /><Stat icon={<Wrench />} value={data.stats.maintenanceOrders} label={messages.directory.orders} /><Stat icon={<Gauge />} value={data.stats.events} label={messages.directory.events} /><Stat icon={<Database />} value={data.stats.componentInstances} label={messages.directory.components} /></div>}
      {query.isLoading && <div className="loading-panel" aria-live="polite">{messages.common.loading}</div>}
      {query.isError && <div className="error-panel" role="alert"><AlertTriangle /><p>{query.error.message}{query.error instanceof PortalApiError && query.error.errorId ? ` · ${messages.common.errorId}: ${query.error.errorId}` : ""}</p><button className="button" onClick={() => void query.refetch()}>{messages.common.retry}</button></div>}
      {data && data.vehicles.length === 0 && <div className="empty-panel">{messages.directory.empty}</div>}
      {data && <div className="vehicle-grid">{data.vehicles.map((vehicle) => <article className="vehicle-card" key={vehicle.plate}><div className="vehicle-card-top"><div><span className="plate">{vehicle.plate}</span><h3>{vehicle.manufacturer} {vehicle.model}</h3></div><span className={`identity-badge ${vehicle.vin ? "ready" : "pending"}`}>VIN {vehicle.vin ?? "-"}</span></div><dl className="vehicle-meta"><Meta label={messages.directory.class} value={vehicle.vehicleClass} /><Meta label={messages.directory.fuel} value={vehicle.fuelType} /><Meta label={messages.directory.department} value={vehicle.department} /></dl><div className="vehicle-counts"><span>{messages.directory.maintenance} <strong>{vehicle.counts.maintenanceOrders}</strong></span><span>{messages.directory.events} <strong>{vehicle.counts.events}</strong></span><span>{messages.directory.components} <strong>{vehicle.counts.componentInstances}</strong></span></div><Link href={vehicle.canonicalPath} className="card-link">{messages.common.view}<ArrowRight aria-hidden="true" /></Link></article>)}</div>}
    </section>
  </main>;
}
function Stat({ icon, value, label }: { icon: React.ReactNode; value: number; label: string }) { return <div className="stat"><span className="stat-icon">{icon}</span><strong>{value}</strong><span>{label}</span></div>; }
function Meta({ label, value }: { label: string; value: string | null }) { return <div><dt>{label}</dt><dd>{value ?? "-"}</dd></div>; }
