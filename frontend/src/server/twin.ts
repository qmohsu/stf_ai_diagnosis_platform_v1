import { PortalError } from "@/server/errors";

const DEFAULT_BASE_URL = "http://127.0.0.1:4173";

export async function twinGet(path: string, timeoutMs = 8_000): Promise<unknown> {
  const base = process.env.VEHICLE_TWIN_API_BASE_URL || DEFAULT_BASE_URL;
  const url = new URL(path, base.endsWith("/") ? base : `${base}/`);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: controller.signal,
    });
    if (response.status === 404) throw new PortalError(404, "TWIN_NOT_FOUND", "Vehicle or resource not found");
    if (!response.ok) throw new PortalError(502, "TWIN_BAD_STATUS", `Vehicle platform returned ${response.status}`);
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) throw new PortalError(502, "TWIN_BAD_CONTENT", "Vehicle platform returned a non-JSON response");
    try {
      return await response.json();
    } catch {
      throw new PortalError(502, "TWIN_BAD_JSON", "Vehicle platform returned invalid JSON");
    }
  } catch (error) {
    if (error instanceof PortalError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new PortalError(504, "TWIN_TIMEOUT", "Vehicle platform request timed out");
    }
    throw new PortalError(503, "TWIN_UNAVAILABLE", "Vehicle platform is unavailable");
  } finally {
    clearTimeout(timer);
  }
}
