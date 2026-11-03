import { NextResponse } from "next/server";
import { PortalError, errorId } from "@/server/errors";

export function portalJson(value: unknown, init?: ResponseInit): NextResponse {
  const response = NextResponse.json(value, init);
  response.headers.set("Cache-Control", "private, no-cache, no-store, must-revalidate");
  return response;
}

export function portalError(error: unknown): NextResponse {
  const known = error instanceof PortalError ? error : new PortalError(500, "PORTAL_INTERNAL", "Unexpected portal error");
  const id = errorId(known.code);
  return portalJson({ error: { code: known.code, message: known.message, errorId: id } }, { status: known.status });
}
