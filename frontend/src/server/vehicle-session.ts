import { cookies } from "next/headers";
import type { NextResponse } from "next/server";
import type { IdentityType, VehicleCard, VehicleVerificationContext } from "@/lib/domain";
import { normalizePlate, normalizeVin } from "@/lib/vehicle";
import { PortalError } from "@/server/errors";

export const VEHICLE_CONTEXT_COOKIE = "portal_vehicle_context";

export async function encodeVehicleContext(vehicle: VehicleCard): Promise<string> {
  if (!vehicle.vin) throw new PortalError(409, "VEHICLE_IDENTITY_INCOMPLETE", "Vehicle identity data is not yet complete");
  const context: VehicleVerificationContext = {
    plate: vehicle.plate,
    vin: vehicle.vin,
    manufacturer: vehicle.manufacturer,
    model: vehicle.model,
    canonicalPath: vehicle.canonicalPath,
  };
  const payload = base64UrlEncode(JSON.stringify(context));
  return `${payload}.${await sign(payload)}`;
}

export async function decodeVehicleContext(value: string | undefined): Promise<VehicleVerificationContext | null> {
  if (!value) return null;
  const [payload, signature, extra] = value.split(".");
  if (!payload || !signature || extra || !safeEqual(signature, await sign(payload))) return null;
  try {
    const parsed = JSON.parse(base64UrlDecode(payload)) as Partial<VehicleVerificationContext>;
    if (typeof parsed.plate !== "string" || typeof parsed.vin !== "string" || typeof parsed.manufacturer !== "string" || typeof parsed.model !== "string" || typeof parsed.canonicalPath !== "string") return null;
    const plate = normalizePlate(parsed.plate);
    const vin = normalizeVin(parsed.vin);
    if (!plate || !vin || parsed.canonicalPath !== `/vehicles/vin/${encodeURIComponent(vin)}`) return null;
    return { plate, vin, manufacturer: parsed.manufacturer, model: parsed.model, canonicalPath: parsed.canonicalPath };
  } catch { return null; }
}

export async function currentVehicleContext(): Promise<VehicleVerificationContext | null> {
  return await decodeVehicleContext((await cookies()).get(VEHICLE_CONTEXT_COOKIE)?.value);
}

export async function requireVehicleContext(identityType: IdentityType, identity: string): Promise<VehicleVerificationContext> {
  const context = await currentVehicleContext();
  if (!context) throw new PortalError(401, "VEHICLE_CONTEXT_REQUIRED", "Vehicle verification is required");
  if (!matchesVehicleContext(context, identityType, identity)) throw new PortalError(403, "VEHICLE_CONTEXT_MISMATCH", "Verify this vehicle before opening its data");
  return context;
}

export function matchesVehicleContext(context: VehicleVerificationContext, identityType: IdentityType, identity: string): boolean {
  return identityType === "vin" ? normalizeVin(identity) === context.vin : normalizePlate(identity) === context.plate;
}

export async function setVehicleContext(response: NextResponse, vehicle: VehicleCard): Promise<void> {
  response.cookies.set(VEHICLE_CONTEXT_COOKIE, await encodeVehicleContext(vehicle), {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
  });
}

export function clearVehicleContext(response: NextResponse): void {
  response.cookies.set(VEHICLE_CONTEXT_COOKIE, "", {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 0,
  });
}

async function sign(payload: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", encoder.encode(sessionSecret()), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return bytesToBase64Url(new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(payload))));
}
function safeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let different = 0;
  for (let index = 0; index < left.length; index += 1) different |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return different === 0;
}
function base64UrlEncode(value: string): string { return bytesToBase64Url(new TextEncoder().encode(value)); }
function base64UrlDecode(value: string): string {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return new TextDecoder().decode(Uint8Array.from(binary, (character) => character.charCodeAt(0)));
}
function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}
function sessionSecret(): string {
  const configured = process.env.PORTAL_VEHICLE_SESSION_SECRET;
  if (configured && configured.length >= 32) return configured;
  if (process.env.NODE_ENV === "production") throw new PortalError(500, "VEHICLE_SESSION_CONFIG", "Vehicle session secret is not configured");
  return "towngas-portal-local-development-secret";
}
