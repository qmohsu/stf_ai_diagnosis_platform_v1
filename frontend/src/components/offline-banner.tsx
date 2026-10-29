"use client";
import { CloudOff } from "lucide-react";
import { useLocale } from "@/components/locale-provider";
export function OfflineBanner({ cachedAt }: { cachedAt: string | null }) {
  const { messages, locale } = useLocale();
  return <div className="offline-banner" role="status"><CloudOff aria-hidden="true" size={18} /><span>{messages.common.offline}{cachedAt ? ` · ${messages.common.cachedAt}: ${new Date(cachedAt).toLocaleString(locale)}` : ""}</span></div>;
}
