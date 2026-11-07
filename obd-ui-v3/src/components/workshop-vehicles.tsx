"use client";

import Link from "next/link";
import { useDeferredValue, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PortalApiError, portalFetch } from "@/lib/api-client";
import type { VehicleRecord } from "@/lib/v3-domain";

interface VehiclePage { vehicles: VehicleRecord[]; nextOffset: number | null }

export function WorkshopVehicles({ workshopId }: { workshopId: string }) {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useDeferredValue(search.trim());
  const query = useQuery({
    queryKey: ["workshop-vehicles", workshopId, q, offset],
    queryFn: () => portalFetch<VehiclePage>(`/api/portal/workshops/${workshopId}/vehicles?q=${encodeURIComponent(q)}&offset=${offset}`),
  });
  const result = query.data;
  const error = query.error instanceof PortalApiError ? query.error : null;
  return <section className="panel" aria-labelledby="vehicle-list-heading">
    <div className="panel-header"><div><h2 id="vehicle-list-heading">車輛列表</h2><p>依 VIN、車牌或暱稱搜尋</p></div><label className="search-label"><span className="sr-only">搜尋車輛</span><input value={search} onChange={(event) => { setSearch(event.target.value); setOffset(0); }} maxLength={50} placeholder="搜尋 VIN、車牌或暱稱" /></label></div>
    {query.isPending && <div className="state-card" role="status">正在載入車輛…</div>}
    {query.isError && <div className="state-card" role="alert"><strong>{error?.status === 403 ? "無權查看此工作坊" : error?.status === 404 ? "找不到工作坊" : "車輛資料暫時無法載入"}</strong><p>{error?.errorId && `錯誤編號：${error.errorId}`}</p><button className="button" type="button" onClick={() => void query.refetch()}>重試</button></div>}
    {result && result.vehicles.length === 0 && <div className="state-card">{q ? "找不到符合條件的車輛。" : "這個工作坊尚無車輛檔案。"}</div>}
    {result && result.vehicles.length > 0 && <><div className="vehicle-grid">{result.vehicles.map((vehicle) => <Link className="vehicle-card" href={`/vehicles/${vehicle.id}`} key={vehicle.id}>
      <div><span className="vehicle-plate">{vehicle.plate || "未填車牌"}</span><span className="vehicle-arrow" aria-hidden="true">→</span></div>
      <h3>{vehicle.nickname || `${vehicle.manufacturer} ${vehicle.model}`}</h3><p>{vehicle.manufacturer} · {vehicle.model}</p><small>VIN {vehicle.vin}</small>
    </Link>)}</div><nav className="pagination" aria-label="車輛分頁"><button className="button secondary" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一頁</button><span>第 {Math.floor(offset / 50) + 1} 頁</span><button className="button secondary" type="button" disabled={result.nextOffset === null} onClick={() => setOffset(result.nextOffset ?? offset)}>下一頁</button></nav></>}
  </section>;
}
