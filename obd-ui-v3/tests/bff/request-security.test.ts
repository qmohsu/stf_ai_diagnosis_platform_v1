import { afterEach, describe, expect, it, vi } from "vitest";
import { requireSameOrigin } from "@/server/request-security";

afterEach(() => vi.unstubAllEnvs());

describe("public origin guard", () => {
  it("fails closed when production origin is missing", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("PORTAL_PUBLIC_ORIGIN", "");
    expect(() => requireSameOrigin(new Request("https://stf-diagnosis.dev/api/auth/login", { headers: { origin: "https://stf-diagnosis.dev" } }))).toThrowError(expect.objectContaining({ status: 503, code: "ORIGIN_CONFIG" }));
  });

  it("accepts only the configured exact origin", () => {
    vi.stubEnv("PORTAL_PUBLIC_ORIGIN", "https://stf-diagnosis.dev");
    expect(() => requireSameOrigin(new Request("https://stf-diagnosis.dev/api/auth/login", { headers: { origin: "https://stf-diagnosis.dev" } }))).not.toThrow();
    expect(() => requireSameOrigin(new Request("https://stf-diagnosis.dev/api/auth/login", { headers: { origin: "https://other.example" } }))).toThrowError(expect.objectContaining({ status: 403, code: "ORIGIN_REJECTED" }));
  });
});
