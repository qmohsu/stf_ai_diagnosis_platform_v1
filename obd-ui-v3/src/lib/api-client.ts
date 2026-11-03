import { redirectToLogin } from "@/lib/client-session";

export class PortalApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly errorId: string | null,
    message: string,
    public readonly code: string | null = null,
  ) { super(message); }
}

export async function portalFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  if (!response.ok) throw await portalResponseError(response);
  return await response.json() as T;
}

export function portalPost<T>(path: string, value: unknown): Promise<T> {
  return portalFetch<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
}

export async function portalEventStream(path: string, signal: AbortSignal): Promise<Response> {
  const response = await fetch(path, {
    headers: { Accept: "text/event-stream" },
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw await portalResponseError(response);
  if (!response.headers.get("content-type")?.toLowerCase().startsWith("text/event-stream") || !response.body) {
    throw new PortalApiError(502, null, "診斷過程的回應格式不正確", "BAD_EVENT_STREAM");
  }
  return response;
}

async function portalResponseError(response: Response): Promise<PortalApiError> {
  if (response.status === 401 && typeof window !== "undefined") void redirectToLogin();
  let message = `Request failed (${response.status})`;
  let errorId: string | null = null;
  let code: string | null = null;
  try {
    const body = await response.json() as { error?: { message?: string; errorId?: string; code?: string } };
    message = body.error?.message ?? message;
    errorId = body.error?.errorId ?? null;
    code = body.error?.code ?? null;
  } catch { /* The upstream may not have a JSON error body. */ }
  return new PortalApiError(response.status, errorId, message, code);
}
