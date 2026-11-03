import { requirePortalSession } from "@/server/auth-session";
import { checkedUuid, v3PortalResponse } from "@/server/diagnosis-client";
import { PortalError } from "@/server/errors";
import { portalError } from "@/server/http";
import { rateLimit } from "@/server/rate-limit";

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const IMAGE_EXTENSIONS = /\.(png|jpe?g|gif|webp)$/i;

export async function GET(request: Request, context: { params: Promise<{ manualId: string; assetPath: string[] }> }): Promise<Response> {
  const limited = rateLimit(request); if (limited) return limited;
  try {
    const session = await requirePortalSession(request);
    const { manualId: rawManualId, assetPath } = await context.params;
    const manualId = checkedUuid(rawManualId);
    if (!Array.isArray(assetPath) || assetPath.length < 2 || assetPath[0] !== "images"
      || !assetPath.every((part) => part && part !== "." && part !== ".." && !/[\\/\x00-\x1f]/.test(part))
      || !IMAGE_EXTENSIONS.test(assetPath.at(-1) ?? "")) {
      throw new PortalError(404, "NOT_FOUND", "找不到手冊圖片");
    }
    const encodedPath = assetPath.map(encodeURIComponent).join("/");
    const upstream = await v3PortalResponse(`/v3/manuals/${manualId}/assets/${encodedPath}`, session.token, {
      headers: { Accept: "image/png,image/jpeg,image/gif,image/webp" }, signal: request.signal,
    });
    const contentType = upstream.headers.get("content-type")?.split(";")[0].trim().toLowerCase();
    if (!contentType || !IMAGE_TYPES.has(contentType) || !upstream.body) {
      throw new PortalError(502, "V3_BAD_RESPONSE", "手冊圖片格式不正確");
    }
    return new Response(upstream.body, { status: 200, headers: {
      "Content-Type": contentType,
      "Cache-Control": "private, no-cache, no-store, must-revalidate",
      "X-Content-Type-Options": "nosniff",
    } });
  } catch (error) { return portalError(error); }
}
