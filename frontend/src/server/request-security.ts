import { PortalError } from "@/server/errors";

export function requireSameOrigin(request: Request): void {
  const expected = process.env.PORTAL_PUBLIC_ORIGIN;
  const origin = request.headers.get("origin");
  if (expected && origin !== expected) throw new PortalError(403, "ORIGIN_REJECTED", "Request origin was rejected");
}
