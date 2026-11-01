import type { Metadata } from "next";
import { Suspense } from "react";
import { redirect } from "next/navigation";
import { LoginForm } from "@/components/login-form";
import { currentPortalUserSafely } from "@/server/auth-session";

export const metadata: Metadata = { title: "員工登入" };

export default async function LoginPage() {
  if (await currentPortalUserSafely()) redirect("/");
  return <Suspense fallback={<main className="login-page" role="status">載入登入頁…</main>}><LoginForm /></Suspense>;
}
