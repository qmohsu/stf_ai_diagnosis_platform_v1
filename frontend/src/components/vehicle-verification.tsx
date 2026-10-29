"use client";

import { useDeferredValue, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, CheckCircle2, ChevronDown, Info, LoaderCircle } from "lucide-react";
import { useLocale } from "@/components/locale-provider";
import { PortalApiError, portalFetch } from "@/lib/api-client";
import { redirectToLogin } from "@/lib/client-session";
import type { VehicleVerificationCandidate, VehicleVerificationSuggestion } from "@/lib/domain";
import { normalizePlate } from "@/lib/vehicle";

export function VehicleVerification() {
  const { messages } = useLocale();
  const plateListId = useId();
  const brandListId = useId();
  const [plate, setPlate] = useState("");
  const [selected, setSelected] = useState<VehicleVerificationCandidate | null>(null);
  const [vin, setVin] = useState("");
  const [brand, setBrand] = useState("");
  const [brandConfirmed, setBrandConfirmed] = useState(false);
  const [model, setModel] = useState("");
  const [plateOpen, setPlateOpen] = useState(false);
  const [brandOpen, setBrandOpen] = useState(false);
  const [plateIndex, setPlateIndex] = useState(-1);
  const [brandIndex, setBrandIndex] = useState(-1);
  const [loadingCandidate, setLoadingCandidate] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const deferredPlate = useDeferredValue(plate);
  const deferredBrand = useDeferredValue(brand);

  const plates = useQuery({
    queryKey: ["verification", "plates", deferredPlate],
    queryFn: () => portalFetch<VehicleVerificationSuggestion[]>(`/api/portal/verification/plates?q=${encodeURIComponent(deferredPlate)}`),
    enabled: Boolean(normalizePlate(deferredPlate)) && normalizePlate(deferredPlate) !== selected?.plate,
  });
  const plateItems = plates.data?.data ?? [];
  const brands = useQuery({
    queryKey: ["verification", "brands", deferredBrand],
    queryFn: () => portalFetch<string[]>(`/api/portal/verification/brands?q=${encodeURIComponent(deferredBrand)}`),
    enabled: Boolean(selected && deferredBrand.trim()),
  });
  const brandItems = brands.data?.data ?? [];
  const models = useQuery({
    queryKey: ["verification", "models", brand],
    queryFn: () => portalFetch<string[]>(`/api/portal/verification/models?brand=${encodeURIComponent(brand)}`),
    enabled: Boolean(selected && brandConfirmed),
  });
  const modelItems = models.data?.data ?? [];

  const resetVerification = () => setError(null);
  const changePlate = (value: string) => {
    setPlate(value);
    setPlateOpen(Boolean(normalizePlate(value)));
    setPlateIndex(-1);
    resetVerification();
    if (selected && normalizePlate(value) !== selected.plate) {
      setSelected(null); setVin(""); setBrand(""); setBrandConfirmed(false); setModel("");
    }
  };

  const choosePlate = async (suggestion: VehicleVerificationSuggestion) => {
    setPlate(suggestion.plate); setPlateOpen(false); setPlateIndex(-1); setLoadingCandidate(true); setError(null);
    try {
      const response = await portalFetch<VehicleVerificationCandidate>(`/api/portal/verification/vehicles/${encodeURIComponent(suggestion.plate)}`);
      const candidate = response.data;
      setSelected(candidate); setVin(candidate.vin ?? ""); setBrand(candidate.manufacturer); setBrandConfirmed(true); setModel(candidate.model);
      setAnnouncement(`${messages.verification.selected}: ${candidate.plate}, ${candidate.manufacturer}, ${candidate.model}`);
    } catch (caught) { setSelected(null); setVin(""); setBrand(""); setBrandConfirmed(false); setModel(""); setError(apiError(caught, messages.common.error, messages.common.errorId)); }
    finally { setLoadingCandidate(false); }
  };

  const confirmBrand = (standardBrand: string) => {
    const changed = standardBrand !== selected?.manufacturer;
    setBrand(standardBrand); setBrandConfirmed(true); setBrandOpen(false); setBrandIndex(-1); setError(null);
    if (changed) setModel("");
    setAnnouncement(`${messages.common.manufacturer}: ${standardBrand}`);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selected?.verifiable) return;
    if (!plate || !vin || !brandConfirmed || !brand || !model) { setError(messages.verification.failed); return; }
    setSubmitting(true); setError(null);
    try {
      const response = await fetch("/api/portal/verification", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ plate, vin, manufacturer: brand, model }) });
      const body = await response.json() as { canonicalPath?: string; error?: { message?: string; errorId?: string } };
      if (response.status === 401) { await redirectToLogin(); return; }
      if (!response.ok || !body.canonicalPath) throw new PortalApiError(response.status, body.error?.errorId ?? null, body.error?.message ?? messages.verification.failed);
      window.location.assign(body.canonicalPath);
    } catch (caught) {
      const localizedFailure = caught instanceof PortalApiError && caught.status === 422
        ? `${messages.verification.failed}${caught.errorId ? ` · ${messages.common.errorId}: ${caught.errorId}` : ""}`
        : apiError(caught, messages.verification.failed, messages.common.errorId);
      setError(caught instanceof TypeError ? messages.verification.offlineFailed : localizedFailure);
      setSubmitting(false);
    }
  };

  const plateKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") { setPlateOpen(false); return; }
    if (!plateItems.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault(); setPlateOpen(true);
      setPlateIndex((current) => event.key === "ArrowDown" ? (current + 1) % plateItems.length : (current <= 0 ? plateItems.length - 1 : current - 1));
    }
    if (event.key === "Enter" && plateOpen) {
      const item = plateItems[plateIndex >= 0 ? plateIndex : 0];
      if (item) { event.preventDefault(); void choosePlate(item); }
    }
  };

  const brandKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") { setBrandOpen(false); return; }
    if (!brandItems.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault(); setBrandOpen(true);
      setBrandIndex((current) => event.key === "ArrowDown" ? (current + 1) % brandItems.length : (current <= 0 ? brandItems.length - 1 : current - 1));
    }
    if (event.key === "Enter") {
      const exact = brandItems.find((item) => item.toLocaleLowerCase() === brand.trim().toLocaleLowerCase());
      const item = brandItems[brandIndex] ?? exact;
      if (item) { event.preventDefault(); confirmBrand(item); }
    }
  };

  const candidateError = plates.isError ? apiError(plates.error, messages.common.error, messages.common.errorId) : null;
  return <main className="verification-page">
    <h1>{messages.verification.title}</h1>
    <form className="verification-card" onSubmit={submit} noValidate>
      <div className="verification-heading"><div><h2>{selected ? messages.verification.linked : messages.verification.formTitle}</h2></div>{selected?.verifiable && <span className="verification-ready"><CheckCircle2 />{messages.verification.selected}</span>}</div>

      <div className="verification-field autocomplete" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setPlateOpen(false); }}>
        <label htmlFor="vehicle-plate">{messages.common.plate}</label>
        <div className="verification-control"><input id="vehicle-plate" value={plate} autoComplete="off" placeholder={messages.verification.platePlaceholder} role="combobox" aria-autocomplete="list" aria-expanded={plateOpen} aria-controls={plateListId} aria-activedescendant={plateIndex >= 0 ? `${plateListId}-${plateIndex}` : undefined} onChange={(event) => changePlate(event.target.value)} onFocus={() => setPlateOpen(Boolean(normalizePlate(plate)) && normalizePlate(plate) !== selected?.plate)} onKeyDown={plateKeyDown} /><ChevronDown aria-hidden="true" /></div>
        {plateOpen && <div className="verification-options" id={plateListId} role="listbox" aria-label={messages.common.plate}>
          {plates.isLoading && <div className="verification-option-state"><LoaderCircle className="spin" />{messages.common.loading}</div>}
          {!plates.isLoading && candidateError && <div className="verification-option-state error"><AlertTriangle />{candidateError}<button type="button" onClick={() => void plates.refetch()}>{messages.common.retry}</button></div>}
          {!plates.isLoading && !candidateError && plateItems.length === 0 && <div className="verification-option-state">{messages.verification.noPlate}</div>}
          {!plates.isLoading && !candidateError && plateItems.length > 0 && <><div className="verification-match-count">{messages.verification.matches} {plateItems.length} {messages.verification.vehicles}</div>{plateItems.map((item, index) => <button type="button" role="option" aria-selected={index === plateIndex} id={`${plateListId}-${index}`} className={index === plateIndex ? "active" : ""} key={item.plate} onMouseDown={(event) => event.preventDefault()} onClick={() => void choosePlate(item)}><strong>{item.plate}</strong><span>{item.vinState === "ready" ? messages.verification.fullPlate : messages.common.pending}</span></button>)}</>}
        </div>}
      </div>

      <div className="verification-field">
        <label htmlFor="vehicle-vin">{messages.common.vin}</label>
        <input id="vehicle-vin" value={selected && !selected.verifiable ? "-" : vin} disabled={!selected || !selected.verifiable} placeholder={messages.verification.selectPlateFirst} onChange={(event) => { setVin(event.target.value); resetVerification(); }} />
      </div>

      <div className="verification-field autocomplete" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setBrandOpen(false); }}>
        <label htmlFor="vehicle-brand">{messages.common.manufacturer}</label>
        <div className="verification-control"><input id="vehicle-brand" value={brand} disabled={!selected} autoComplete="off" placeholder={messages.verification.selectPlateFirst} role="combobox" aria-autocomplete="list" aria-expanded={brandOpen} aria-controls={brandListId} aria-activedescendant={brandIndex >= 0 ? `${brandListId}-${brandIndex}` : undefined} onChange={(event) => { setBrand(event.target.value); setBrandConfirmed(false); setModel(""); setBrandOpen(true); setBrandIndex(-1); resetVerification(); }} onFocus={() => selected && setBrandOpen(true)} onKeyDown={brandKeyDown} />{brandConfirmed ? <CheckCircle2 className="control-check" aria-label={messages.verification.selected} /> : <ChevronDown aria-hidden="true" />}</div>
        {selected && brandOpen && <div className="verification-options" id={brandListId} role="listbox" aria-label={messages.common.manufacturer}>
          {brands.isLoading && <div className="verification-option-state"><LoaderCircle className="spin" />{messages.common.loading}</div>}
          {!brands.isLoading && brandItems.length === 0 && <div className="verification-option-state">{messages.common.noRecords}</div>}
          {brandItems.map((item, index) => <button type="button" role="option" aria-selected={index === brandIndex || brandConfirmed && item === brand} id={`${brandListId}-${index}`} className={index === brandIndex ? "active" : ""} key={item} onMouseDown={(event) => event.preventDefault()} onClick={() => confirmBrand(item)}>{brandConfirmed && item === brand && <Check aria-hidden="true" />}<strong>{item}</strong></button>)}
        </div>}
      </div>

      <div className="verification-field">
        <label htmlFor="vehicle-model">{messages.common.model}</label>
        <div className="verification-control"><select id="vehicle-model" value={model} disabled={!selected || !brandConfirmed} onChange={(event) => { setModel(event.target.value); resetVerification(); }}><option value="">{brandConfirmed ? messages.common.model : messages.verification.confirmBrandFirst}</option>{[...new Set(model ? [model, ...modelItems] : modelItems)].map((item) => <option value={item} key={item}>{item}</option>)}</select><ChevronDown aria-hidden="true" /></div>
      </div>

      {!selected && <p className="verification-help"><Info />{messages.verification.consistency}</p>}
      {loadingCandidate && <p className="verification-banner neutral"><LoaderCircle className="spin" />{messages.common.loading}</p>}
      {selected && !selected.verifiable && <p className="verification-banner warning"><AlertTriangle />{messages.verification.sourcePending}</p>}
      {selected?.verifiable && <p className="verification-banner success"><CheckCircle2 />{messages.verification.consistency}</p>}
      {error && <p className="verification-error" role="alert"><AlertTriangle />{error}</p>}
      {selected && <button className="verification-submit" type="submit" disabled={!selected.verifiable || submitting}>{submitting && <LoaderCircle className="spin" />}{submitting ? messages.verification.verifying : messages.verification.verify}</button>}
      <span className="sr-only" aria-live="polite">{announcement || (plateOpen && !plates.isLoading ? `${messages.verification.matches} ${plateItems.length} ${messages.verification.vehicles}` : "")}</span>
    </form>
  </main>;
}

function apiError(error: unknown, fallback: string, errorIdLabel: string): string {
  if (error instanceof PortalApiError) return `${error.message}${error.errorId ? ` · ${errorIdLabel}: ${error.errorId}` : ""}`;
  return fallback;
}
