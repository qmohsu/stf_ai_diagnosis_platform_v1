import { cache } from "react";
import { cookies } from "next/headers";
import type { NextResponse } from "next/server";
import { PortalError } from "@/server/errors";

export const AUTH_SESSION_COOKIE = "portal_access_token";

const V3_TIMEOUT_MS = 10_000;

export interface PortalMembership {
  workshop_id: string;
  workshop_name: string;
  role: string;
}

export interface PortalUser {
  id: string;
  username: string;
  email: string | null;
  display_name: string | null;
  memberships: PortalMembership[];
}

export interface PortalSession {
  token: string;
  user: PortalUser;
}

export interface LoginResult extends PortalSession {
  expiresAt: Date;
}

export async function loginWithV3(username: string, password: string): Promise<LoginResult> {
  const body = new URLSearchParams({ username, password });
  const response = await v3Fetch("/v3/auth/login", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });

  if (response.status === 400) {
    throw new PortalError(401, "AUTH_INVALID_CREDENTIALS", "Username or password is incorrect");
  }
  if (response.status === 422) {
    throw new PortalError(400, "AUTH_INVALID_REQUEST", "Username and password are required");
  }
  if (!response.ok) {
    throw new PortalError(503, "AUTH_SERVICE_UNAVAILABLE", "Authentication service is unavailable");
  }

  let bodyValue: unknown;
  try { bodyValue = await response.json(); }
  catch { throw invalidAuthResponse(); }
  if (!isBearerResponse(bodyValue)) throw invalidAuthResponse();

  const expiresAt = jwtExpiration(bodyValue.access_token);
  const user = await userForToken(bodyValue.access_token);
  if (!user) throw new PortalError(401, "AUTH_INVALID_CREDENTIALS", "Username or password is incorrect");
  return { token: bodyValue.access_token, user, expiresAt };
}

export async function currentPortalSession(): Promise<PortalSession | null> {
  const token = (await cookies()).get(AUTH_SESSION_COOKIE)?.value;
  return token ? validatedSession(token) : null;
}

export async function currentPortalUserSafely(): Promise<PortalUser | null> {
  try { return (await currentPortalSession())?.user ?? null; }
  catch { return null; }
}

export async function requirePortalUser(request: Request): Promise<PortalUser> {
  const token = accessTokenFromRequest(request);
  if (!token) throw new PortalError(401, "AUTH_REQUIRED", "Sign in is required");
  const session = await validatedSession(token);
  if (!session) throw new PortalError(401, "AUTH_REQUIRED", "Sign in is required");
  return session.user;
}

export function accessTokenFromRequest(request: Request): string | null {
  const cookie = request.headers.get("cookie");
  if (!cookie) return null;
  for (const pair of cookie.split(";")) {
    const separator = pair.indexOf("=");
    if (separator < 0) continue;
    const name = pair.slice(0, separator).trim();
    if (name !== AUTH_SESSION_COOKIE) continue;
    try { return decodeURIComponent(pair.slice(separator + 1).trim()); }
    catch { return null; }
  }
  return null;
}

export function setPortalSession(response: NextResponse, token: string, expiresAt: Date): void {
  const maxAge = Math.max(0, Math.floor((expiresAt.getTime() - Date.now()) / 1000));
  response.cookies.set(AUTH_SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    expires: expiresAt,
    maxAge,
  });
}

export function clearPortalSession(response: NextResponse): void {
  response.cookies.set(AUTH_SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 0,
  });
}

export async function logoutFromV3(token: string | null): Promise<void> {
  if (!token) return;
  try {
    await v3Fetch("/v3/auth/logout", {
      method: "POST",
      headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
    });
  } catch { /* Local logout must succeed even when V3 is unavailable. */ }
}

const validatedSession = cache(async (token: string): Promise<PortalSession | null> => {
  const user = await userForToken(token);
  return user ? { token, user } : null;
});

async function userForToken(token: string): Promise<PortalUser | null> {
  const response = await v3Fetch("/v3/users/me", {
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
  });
  if (response.status === 401 || response.status === 403) return null;
  if (!response.ok) throw new PortalError(503, "AUTH_SERVICE_UNAVAILABLE", "Authentication service is unavailable");
  let value: unknown;
  try { value = await response.json(); }
  catch { throw invalidAuthResponse(); }
  if (!isPortalUser(value)) throw invalidAuthResponse();
  return value;
}

async function v3Fetch(path: string, init: RequestInit): Promise<Response> {
  let baseUrl = process.env.STF_V3_API_BASE_URL?.trim();
  if (!baseUrl) {
    if (process.env.NODE_ENV === "production") {
      throw new PortalError(503, "AUTH_SERVICE_CONFIG", "Authentication service is not configured");
    }
    baseUrl = "http://127.0.0.1:8002";
  }
  try {
    return await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(V3_TIMEOUT_MS),
    });
  } catch {
    throw new PortalError(503, "AUTH_SERVICE_UNAVAILABLE", "Authentication service is unavailable");
  }
}

function jwtExpiration(token: string): Date {
  const parts = token.split(".");
  if (parts.length !== 3) throw invalidAuthResponse();
  try {
    const normalized = parts[1].replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(parts[1].length / 4) * 4, "=");
    const payload = JSON.parse(atob(normalized)) as { exp?: unknown };
    if (typeof payload.exp !== "number" || !Number.isFinite(payload.exp)) throw invalidAuthResponse();
    const expiresAt = new Date(payload.exp * 1000);
    if (!Number.isFinite(expiresAt.getTime()) || expiresAt.getTime() <= Date.now()) throw invalidAuthResponse();
    return expiresAt;
  } catch (error) {
    if (error instanceof PortalError) throw error;
    throw invalidAuthResponse();
  }
}

function isBearerResponse(value: unknown): value is { access_token: string; token_type: string } {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return typeof item.access_token === "string" && item.access_token.length > 0 && item.token_type === "bearer";
}

function isPortalUser(value: unknown): value is PortalUser {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return typeof item.id === "string"
    && typeof item.username === "string"
    && (item.email === null || typeof item.email === "string")
    && (item.display_name === null || typeof item.display_name === "string")
    && Array.isArray(item.memberships)
    && item.memberships.every((membership) => {
      if (typeof membership !== "object" || membership === null) return false;
      const entry = membership as Record<string, unknown>;
      return typeof entry.workshop_id === "string" && typeof entry.workshop_name === "string" && typeof entry.role === "string";
    });
}

function invalidAuthResponse(): PortalError {
  return new PortalError(503, "AUTH_SERVICE_UNAVAILABLE", "Authentication service returned an invalid response");
}
