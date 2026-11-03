import type { Metadata } from "next";
import { Suspense } from "react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { LoginForm } from "@/components/login-form";
import { currentPortalSession } from "@/server/auth-session";

export const metadata: Metadata = { title: "員工登入" };

export default async function LoginPage() {
  let session;
  try {
    session = await currentPortalSession();
  } catch {
    return <main className="login-page"><div className="state-card" role="alert">
      <h1>服務暫時無法使用</h1>
      <p>無法確認目前的登入狀態。請稍後重新整理，登入資料不會因此清除。</p>
      <Link className="button" href="/login">重新整理</Link>
    </div></main>;
  }
  if (session) redirect("/");
  return <Suspense fallback={<main className="login-page" role="status">載入登入頁…</main>}><LoginForm /></Suspense>;
}
