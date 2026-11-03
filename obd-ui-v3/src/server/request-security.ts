import { PortalError } from "@/server/errors";

export function requireSameOrigin(request: Request): void {
  const expected = process.env.PORTAL_PUBLIC_ORIGIN;
  if (!expected && process.env.NODE_ENV === "production") {
    throw new PortalError(503, "ORIGIN_CONFIG", "公開網站來源尚未設定");
  }
  const origin = request.headers.get("origin");
  if (expected && origin !== expected) throw new PortalError(403, "ORIGIN_REJECTED", "Request origin was rejected");
}
