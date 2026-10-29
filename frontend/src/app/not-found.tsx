"use client";
import Link from "next/link";
import { useLocale } from "@/components/locale-provider";

export default function NotFound() {
  const { messages } = useLocale();
  return <main className="not-found"><h1>404</h1><p>{messages.notFound.title}</p><Link className="button" href="/">{messages.notFound.back}</Link></main>;
}
