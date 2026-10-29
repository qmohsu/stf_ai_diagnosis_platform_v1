"use client";

import type { DataState } from "@/lib/domain";
import { useLocale } from "@/components/locale-provider";

export function DataStateView<T>({ state, render, onRetry }: { state: DataState<T>; render: (value: T) => React.ReactNode; onRetry?: () => void }) {
  const { messages } = useLocale();
  if (state.kind === "ready") return <>{render(state.value)}</>;
  if (state.kind === "pendingSource") return <span className="state-placeholder" aria-label={messages.common.pending}>-</span>;
  if (state.kind === "deferred") return <span className="state-deferred">{messages.common.deferred}</span>;
  if (state.kind === "empty") return <span>{messages.common.noRecords}</span>;
  return <div className="state-error" role="alert"><span>{state.message} ({messages.common.errorId}: {state.errorId})</span>{onRetry && <button type="button" className="button secondary" onClick={onRetry}>{messages.common.retry}</button>}</div>;
}
