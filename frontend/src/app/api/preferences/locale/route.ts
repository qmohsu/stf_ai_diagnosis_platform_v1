import { NextResponse } from "next/server";
import { isLocale } from "@/lib/portal-i18n";
export async function POST(request: Request) {
  const origin = request.headers.get("origin"); const expected = process.env.PORTAL_PUBLIC_ORIGIN;
  if (expected && origin && origin !== expected) return NextResponse.json({ error: "origin rejected" }, { status: 403 });
  let body: unknown; try { body = await request.json(); } catch { return NextResponse.json({ error: "invalid JSON" }, { status: 400 }); }
  const locale = typeof body === "object" && body !== null && "locale" in body ? (body as { locale?: unknown }).locale : null;
  if (!isLocale(locale)) return NextResponse.json({ error: "invalid locale" }, { status: 400 });
  const response = NextResponse.json({ locale }); response.cookies.set("portal_locale", locale, { httpOnly: false, sameSite: "strict", secure: process.env.NODE_ENV === "production", path: "/", maxAge: 365 * 24 * 60 * 60 }); return response;
}
