"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { PortalApiError, portalPost } from "@/lib/api-client";
import type { VehicleRecord } from "@/lib/v3-domain";

export function CreateVehicleForm({ workshopId }: { workshopId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ vin: "", manufacturer: "", model: "", plate: "", nickname: "" });
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await portalPost<{ vehicle: VehicleRecord }>(`/api/portal/workshops/${workshopId}/vehicles`, {
        vin: form.vin.trim().toUpperCase(), manufacturer: form.manufacturer.trim(), model: form.model.trim(),
        plate: form.plate.trim() || null, nickname: form.nickname.trim() || null,
      });
      router.push(`/vehicles/${result.vehicle.id}`);
    } catch (cause) {
      setError(cause instanceof PortalApiError ? cause.message : "建立車檔失敗，請重試");
    } finally { setBusy(false); }
  }
  return <section className="panel" aria-labelledby="create-vehicle-heading">
    <div className="section-heading"><div><h2 id="create-vehicle-heading">建立車輛檔案</h2><p>每台車以 VIN 識別，建立後 VIN 不能更改。</p></div>
      <button className="button secondary" type="button" onClick={() => setOpen(!open)}>{open ? "收起" : "建立車檔"}</button></div>
    {open && <form className="portal-form" onSubmit={submit}>
      <label>VIN（17 碼）<input required minLength={17} maxLength={17} autoCapitalize="characters" value={form.vin} onChange={(e) => setForm({ ...form, vin: e.target.value.toUpperCase() })} /></label>
      <label>品牌<input required maxLength={100} value={form.manufacturer} onChange={(e) => setForm({ ...form, manufacturer: e.target.value })} /></label>
      <label>型號<input required maxLength={100} value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></label>
      <label>車牌（選填）<input maxLength={20} value={form.plate} onChange={(e) => setForm({ ...form, plate: e.target.value })} /></label>
      <label>暱稱（選填）<input maxLength={100} value={form.nickname} onChange={(e) => setForm({ ...form, nickname: e.target.value })} /></label>
      {error && <p className="action-error" role="alert">{error}</p>}
      <button className="button" type="submit" disabled={busy}>{busy ? "建立中…" : "儲存車檔"}</button>
    </form>}
  </section>;
}
