"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PortalApiError, portalFetch, portalPost } from "@/lib/api-client";
import type { DeviceRecord } from "@/server/device-client";

export function DeviceCredentials({ vehicleId }: { vehicleId: string }) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const query = useQuery({
    queryKey: ["vehicle-devices", vehicleId],
    queryFn: () => portalFetch<{ devices: DeviceRecord[] }>(`/api/portal/vehicles/${vehicleId}/devices`),
  });
  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setToken(null);
    try {
      const result = await portalPost<{ device: DeviceRecord; token: string }>(`/api/portal/vehicles/${vehicleId}/devices`, { label: label.trim() });
      setToken(result.token); setLabel("");
      void queryClient.invalidateQueries({ queryKey: ["vehicle-devices", vehicleId] });
    } catch (cause) { setError(cause instanceof PortalApiError ? cause.message : "無法發放設備憑證"); }
    finally { setBusy(false); }
  }
  async function revoke(device: DeviceRecord) {
    if (!window.confirm(`確定吊銷「${device.label}」的設備憑證？吊銷後此設備將無法上傳。`)) return;
    setBusy(true); setError(""); setToken(null);
    try {
      await portalFetch<{ revoked: true }>(`/api/portal/devices/${device.id}`, { method: "DELETE" });
      void queryClient.invalidateQueries({ queryKey: ["vehicle-devices", vehicleId] });
    } catch (cause) { setError(cause instanceof PortalApiError ? cause.message : "無法吊銷設備憑證"); }
    finally { setBusy(false); }
  }
  return <section className="panel device-panel" aria-labelledby="device-heading">
    <div className="section-heading"><div><h2 id="device-heading">設備憑證</h2><p>僅工作坊管理員可發放與吊銷。每台車使用自己的憑證。</p></div></div>
    <form className="portal-form" onSubmit={create}><label>設備名稱<input value={label} maxLength={100} required onChange={(e) => setLabel(e.target.value)} placeholder="例如：車載 Jetson A" /></label>
      <button className="button" disabled={busy || !label.trim()} type="submit">發放憑證</button></form>
    {token && <div className="token-once" role="status"><strong>請現在由人員安全保存此憑證；離開頁面後不能再次查看。</strong><code>{token}</code><button className="button secondary" type="button" onClick={() => setToken(null)}>已保存，隱藏憑證</button></div>}
    {error && <p className="action-error" role="alert">{error}</p>}
    {query.isPending && <p className="inline-state" role="status">正在載入設備…</p>}
    {query.isError && <p className="action-error" role="alert">設備列表暫時無法載入。<button className="button secondary" onClick={() => void query.refetch()}>重試</button></p>}
    {query.data && <ul className="record-list">{query.data.devices.map((device) => <li className="record-item" key={device.id}>
      <div className="record-copy"><strong>{device.label}</strong><small>{device.revoked_at ? "已吊銷" : device.last_seen_at ? `最後上傳：${new Date(device.last_seen_at).toLocaleString("zh-TW")}` : "尚未上傳"}</small></div>
      {!device.revoked_at && <button className="button secondary" disabled={busy} type="button" onClick={() => void revoke(device)}>吊銷</button>}
    </li>)}</ul>}
  </section>;
}
