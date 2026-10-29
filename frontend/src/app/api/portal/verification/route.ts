import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";
import { requireSameOrigin } from "@/server/request-security";
import { clearVehicleContext, setVehicleContext } from "@/server/vehicle-session";
import { verifyVehicleIdentity, type VerificationInput } from "@/server/vehicle-verification";

export async function POST(request: Request) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    await requirePortalUser(request);
    let body: unknown;
    try { body = await request.json(); } catch { throw new PortalError(400, "INVALID_JSON", "Request body must be valid JSON"); }
    if (!isVerificationInput(body)) throw new PortalError(400, "INVALID_VERIFICATION_REQUEST", "Vehicle verification requires four text fields");
    const vehicle = await verifyVehicleIdentity(body);
    const response = portalJson({ canonicalPath: vehicle.canonicalPath });
    await setVehicleContext(response, vehicle);
    return response;
  } catch (error) { return portalError(error); }
}

export async function DELETE(request: Request) {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    await requirePortalUser(request);
    const response = portalJson({ cleared: true });
    clearVehicleContext(response);
    return response;
  } catch (error) { return portalError(error); }
}

function isVerificationInput(value: unknown): value is VerificationInput {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return [item.plate, item.vin, item.manufacturer, item.model].every((field) => typeof field === "string");
}

import { requirePortalUser } from "@/server/auth-session";
