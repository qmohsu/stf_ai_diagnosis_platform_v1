import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { LoginForm } from "@/components/login-form";
import { currentPortalUserSafely } from "@/server/auth-session";

export const metadata: Metadata = { title: "員工登入" };

export default async function LoginPage() {
  if (await currentPortalUserSafely()) redirect("/");
  return <LoginForm />;
}
