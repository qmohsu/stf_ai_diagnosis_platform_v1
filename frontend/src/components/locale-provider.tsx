"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { dictionaries, type Dictionary } from "@/lib/portal-i18n";
import type { Locale } from "@/lib/domain";

interface LocaleContextValue {
  locale: Locale;
  messages: Dictionary;
  setLocale: (locale: Locale) => Promise<void>;
  announcement: string;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function LocaleProvider({ initialLocale, children }: { initialLocale: Locale; children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const [announcement, setAnnouncement] = useState("");
  const router = useRouter();

  useEffect(() => {
    if (!("BroadcastChannel" in window)) return;
    const channel = new BroadcastChannel("towngas-portal-locale");
    channel.onmessage = (event: MessageEvent<{ locale?: Locale }>) => {
      const next = event.data?.locale;
      if (next === "zh-HK" || next === "zh-CN") {
        setLocaleState(next);
        setAnnouncement(next === "zh-HK" ? "已切換至繁體中文" : "已切换至简体中文");
        router.refresh();
      }
    };
    return () => channel.close();
  }, [router]);

  const setLocale = useCallback(async (next: Locale) => {
    if (next === locale) return;
    let response: Response;
    try {
      response = await fetch("/api/preferences/locale", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ locale: next }) });
    } catch { return; }
    if (!response.ok) return;
    setLocaleState(next);
    setAnnouncement(next === "zh-HK" ? "已切換至繁體中文" : "已切换至简体中文");
    if ("BroadcastChannel" in window) {
      const channel = new BroadcastChannel("towngas-portal-locale");
      channel.postMessage({ locale: next });
      channel.close();
    }
    router.refresh();
  }, [locale, router]);

  const value = useMemo(() => ({ locale, messages: dictionaries[locale], setLocale, announcement }), [announcement, locale, setLocale]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
  const context = useContext(LocaleContext);
  if (!context) throw new Error("useLocale must be used inside LocaleProvider");
  return context;
}
