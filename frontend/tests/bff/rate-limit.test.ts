import { beforeEach, describe, expect, it } from "vitest";
import { authRateLimit, rateLimit, resetRateLimitsForTests } from "@/server/rate-limit";
beforeEach(resetRateLimitsForTests);
describe("anonymous rate limit", () => { it("allows 120 GETs per minute and rejects the next one", () => { const request = new Request("http://portal/api/portal/vehicles", { headers: { "x-forwarded-for": "203.0.113.8" } }); for (let index = 0; index < 120; index++) expect(rateLimit(request)).toBeNull(); expect(rateLimit(request)?.status).toBe(429); }); });
describe("login rate limit", () => { it("allows a burst of 10 attempts and rejects the next one", () => { const request = new Request("http://portal/api/auth/login", { headers: { "x-forwarded-for": "203.0.113.9" } }); for (let index = 0; index < 10; index++) expect(authRateLimit(request)).toBeNull(); expect(authRateLimit(request)?.status).toBe(429); }); });
