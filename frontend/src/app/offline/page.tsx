"use client";
import Link from "next/link";
import { CloudOff } from "lucide-react";
import { useLocale } from "@/components/locale-provider";
export default function OfflinePage() { const { messages } = useLocale(); return <main className="offline-page"><CloudOff aria-hidden="true" /><h1>{messages.offlinePage.title}</h1><p>{messages.offlinePage.body}</p><Link className="button" href="/login">{messages.offlinePage.back}</Link></main>; }
