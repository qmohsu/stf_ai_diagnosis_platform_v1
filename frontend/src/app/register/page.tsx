import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { RegisterForm } from "@/components/register-form";
import { currentPortalUserSafely } from "@/server/auth-session";

export const metadata: Metadata = { title: "邀請註冊" };

export default async function RegisterPage() {
  if (await currentPortalUserSafely()) redirect("/");
  return <RegisterForm />;
}
