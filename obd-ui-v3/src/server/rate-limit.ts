import { NextResponse } from "next/server";

type Bucket = { tokens: number; updatedAt: number };
const buckets = new Map<string, Bucket>();
const authBuckets = new Map<string, Bucket>();
const CAPACITY = 120;
const REFILL_PER_MS = CAPACITY / 60_000;
const AUTH_CAPACITY = 10;
const AUTH_REFILL_PER_MS = 5 / 1_000;

function clientIp(request: Request): string {
  // PolyU Nginx overwrites X-Real-IP after trusting only the local tunnel.
  // Never use the client-controlled first X-Forwarded-For hop as a bucket key.
  return request.headers.get("x-real-ip")?.trim()
    || (process.env.NODE_ENV === "production" ? "unknown" : request.headers.get("x-forwarded-for")?.split(",")[0]?.trim())
    || "unknown";
}

export function rateLimit(request: Request): NextResponse | null {
  return consume(buckets, clientIp(request), CAPACITY, REFILL_PER_MS);
}

export function authRateLimit(request: Request): NextResponse | null {
  return consume(authBuckets, clientIp(request), AUTH_CAPACITY, AUTH_REFILL_PER_MS);
}

function consume(store: Map<string, Bucket>, key: string, capacity: number, refillPerMs: number): NextResponse | null {
  const now = Date.now();
  const current = store.get(key) ?? { tokens: capacity, updatedAt: now };
  current.tokens = Math.min(capacity, current.tokens + (now - current.updatedAt) * refillPerMs);
  current.updatedAt = now;
  if (current.tokens < 1) {
    store.set(key, current);
    return NextResponse.json(
      { error: { code: "RATE_LIMITED", message: "Too many requests" } },
      { status: 429, headers: { "Retry-After": "1", "Cache-Control": "no-store" } },
    );
  }
  current.tokens -= 1;
  store.set(key, current);
  if (store.size > 10_000) {
    for (const [bucketKey, bucket] of store) {
      if (now - bucket.updatedAt > 120_000) store.delete(bucketKey);
    }
  }
  return null;
}

export function resetRateLimitsForTests(): void {
  buckets.clear();
  authBuckets.clear();
}
