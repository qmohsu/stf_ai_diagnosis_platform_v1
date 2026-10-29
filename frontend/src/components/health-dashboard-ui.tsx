import type { ReactNode } from "react";

export const DASH = "-";

export function Kpi({ tone, icon, label, value, testId }: { tone: string; icon: ReactNode; label: string; value: string; testId?: string }) {
  return <div className={`health-kpi ${tone}`} data-testid={testId}><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></div>;
}

export function HealthTitle({ tone, icon, text }: { tone: string; icon: ReactNode; text: string }) {
  return <div className={`health-title ${tone}`}><span>{icon}</span><h2>{text}</h2></div>;
}

export function Field({ label, value, wide = false }: { label: string; value: ReactNode; wide?: boolean }) {
  return <div className={`health-field${wide ? " wide" : ""}`}><span>{label}</span><strong>{value || DASH}</strong></div>;
}
