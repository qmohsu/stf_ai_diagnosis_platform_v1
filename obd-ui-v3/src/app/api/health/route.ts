import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const base = process.env.STF_V3_API_BASE_URL?.trim();
  if (!base) return NextResponse.json({ status: "degraded", v3Api: "unconfigured" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/v3/health`, { cache: "no-store", signal: AbortSignal.timeout(2_000) });
    if (!response.ok) throw new Error("V3 health failed");
    const value = await response.json() as { status?: unknown };
    if (value.status !== "ok") throw new Error("V3 health degraded");
    return NextResponse.json({ status: "ok", v3Api: "ok" }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ status: "degraded", v3Api: "unavailable" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
