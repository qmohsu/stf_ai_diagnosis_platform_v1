"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: 2, staleTime: 30_000, gcTime: 30 * 60_000, refetchOnWindowFocus: false } } }));
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
