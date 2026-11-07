import { withAccessLog } from "@/server/access-log";
import { PortalError } from "@/server/errors";
import { portalError, portalJson } from "@/server/http";
import { authRateLimit } from "@/server/rate-limit";
import { requireSameOrigin } from "@/server/request-security";

const USERNAME = /^[A-Za-z0-9._-]{3,50}$/;

async function POSTHandler(request: Request) {
  const limited = authRateLimit(request); if (limited) return limited;
  try {
    requireSameOrigin(request);
    const body: unknown = await request.json().catch(() => null);
    if (!validRegistration(body)) throw new PortalError(400, "INVALID_REGISTRATION", "註冊資料不正確");
    const base = process.env.STF_V3_API_BASE_URL?.trim();
    if (!base) throw new PortalError(503, "V3_CONFIG", "V3 服務尚未設定");
    let response: Response;
    try {
      response = await fetch(`${base.replace(/\/$/, "")}/v3/auth/register`, {
        method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
          username: body.username, password: body.password, invite_code: body.invite_code,
          email: body.email ?? null, display_name: body.display_name ?? null,
        }), cache: "no-store", signal: AbortSignal.timeout(10_000),
      });
    } catch { throw new PortalError(503, "V3_UNAVAILABLE", "V3 服務暫時無法使用"); }
    if (!response.ok) {
      let code: unknown;
      try { code = (await response.json() as { code?: unknown }).code; } catch { /* No details needed. */ }
      if (response.status === 422 && code === "invite_code_invalid") throw new PortalError(422, "INVITE_INVALID", "邀請碼無效、已使用或已過期");
      if (response.status === 409 && code === "username_exists") throw new PortalError(409, "USERNAME_EXISTS", "用戶名稱已被使用");
      if (response.status === 409 && code === "email_exists") throw new PortalError(409, "EMAIL_EXISTS", "電子郵件已被使用");
      if (response.status === 422) throw new PortalError(422, "INVALID_REGISTRATION", "註冊資料不正確");
      throw new PortalError(503, "V3_UNAVAILABLE", "V3 服務暫時無法使用");
    }
    return portalJson({ registered: true }, { status: 201 });
  } catch (error) { return portalError(error); }
}

function validRegistration(value: unknown): value is {
  username: string; password: string; invite_code: string; email?: string | null; display_name?: string | null;
} {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.username === "string" && USERNAME.test(item.username)
    && typeof item.password === "string" && item.password.length >= 10 && item.password.length <= 256
    && typeof item.invite_code === "string" && item.invite_code.length >= 1 && item.invite_code.length <= 32
    && (item.email === undefined || item.email === null || (typeof item.email === "string" && item.email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(item.email)))
    && (item.display_name === undefined || item.display_name === null || (typeof item.display_name === "string" && item.display_name.length <= 100));
}

export const runtime = "nodejs";
export const POST = withAccessLog("/api/auth/register", POSTHandler);
