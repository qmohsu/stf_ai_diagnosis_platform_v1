import { NextResponse } from "next/server";
import { twinGet } from "@/server/twin";

export const dynamic = "force-dynamic";

export async function GET() {
  const startedAt = Date.now();
  try {
    await twinGet("/api/vehicles", 2_000);
    return NextResponse.json({ status: "ok", vehiclePlatform: "ok", durationMs: Date.now() - startedAt });
  } catch {
    return NextResponse.json({ status: "degraded", vehiclePlatform: "unavailable", durationMs: Date.now() - startedAt }, { status: 503 });
  }
}
