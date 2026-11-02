"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { PortalApiError, portalFetch } from "@/lib/api-client";
import type { VehicleRecord } from "@/lib/v3-domain";
import { VehicleDiagnosisSections } from "@/components/vehicle-diagnosis-sections";
import { DeviceCredentials } from "@/components/device-credentials";

export function VehicleDetails({ vehicleId, managerWorkshopIds }: { vehicleId: string; managerWorkshopIds: string[] }) {
  const query = useQuery({ queryKey: ["vehicle", vehicleId], queryFn: () => portalFetch<{ vehicle: VehicleRecord }>(`/api/portal/vehicles/${vehicleId}`) });
  const vehicle = query.data?.vehicle;
  const error = query.error instanceof PortalApiError ? query.error : null;
  if (query.isPending) return <main className="page-shell"><div className="state-card" role="status">正在載入車輛檔案…</div></main>;
  if (query.isError) return <main className="page-shell"><div className="state-card" role="alert"><h1>{error?.status === 403 ? "無權查看此車輛" : error?.status === 404 ? "找不到車輛" : "車輛檔案暫時無法載入"}</h1><p>{error?.errorId && `錯誤編號：${error.errorId}`}</p><button type="button" className="button" onClick={() => void query.refetch()}>重試</button></div></main>;
  if (!vehicle) return null;
  return <main className="page-shell"><Link className="back-link" href={`/workshops/${vehicle.workshop_id}`}>← 返回車輛列表</Link>
    <div className="page-heading"><span className="eyebrow">車輛檔案</span><h1>{vehicle.nickname || `${vehicle.manufacturer} ${vehicle.model}`}</h1><p>這是 V3 後端保存的車輛基本資料。</p></div>
    <section className="panel" aria-label="車輛基本資料"><dl className="details-grid"><div><dt>VIN</dt><dd>{vehicle.vin}</dd></div><div><dt>車牌</dt><dd>{vehicle.plate || "未填"}</dd></div><div><dt>品牌</dt><dd>{vehicle.manufacturer}</dd></div><div><dt>型號</dt><dd>{vehicle.model}</dd></div><div><dt>暱稱</dt><dd>{vehicle.nickname || "未填"}</dd></div></dl></section>
    <VehicleDiagnosisSections vehicleId={vehicleId} />
    {managerWorkshopIds.includes(vehicle.workshop_id) && <DeviceCredentials vehicleId={vehicleId} />}
  </main>;
}
