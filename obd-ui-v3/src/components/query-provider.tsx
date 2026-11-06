"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: 2, staleTime: 30_000, gcTime: 30 * 60_000, refetchOnWindowFocus: false } } }));
  const [online, setOnline] = useState(true);

  useEffect(() => {
    const offline = () => { client.clear(); setOnline(false); };
    const onlineAgain = () => window.location.reload();
    window.addEventListener("offline", offline);
    window.addEventListener("online", onlineAgain);
    if (!navigator.onLine) offline();
    return () => {
      window.removeEventListener("offline", offline);
      window.removeEventListener("online", onlineAgain);
    };
  }, [client]);

  return <QueryClientProvider client={client}>{online ? children : <main className="offline-page"><h1>目前離線</h1><p>請檢查網絡連線。重新連線後會再次驗證登入狀態及載入車輛資料。</p></main>}</QueryClientProvider>;
}
