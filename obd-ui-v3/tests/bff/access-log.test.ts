import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { gunzipSync } from "node:zlib";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST as login } from "@/app/api/auth/login/route";
import { setAccessUserId, withAccessLog } from "@/server/access-log";
import { resetRateLimitsForTests } from "@/server/rate-limit";

const vehicleId = "11111111-1111-4111-8111-111111111111";
const userId = "22222222-2222-4222-8222-222222222222";
const vinShaped = "IIIIIIIIIIIIIIIII"; // Excluded VIN letters; still exercises broad redaction.
let logDir: string;
let stdout: string[];

beforeEach(async () => {
  logDir = await mkdtemp(path.join(tmpdir(), "portal-access-test-"));
  stdout = [];
  vi.stubEnv("PORTAL_ACCESS_LOG_DIR", logDir);
  vi.spyOn(process.stdout, "write").mockImplementation((chunk) => { stdout.push(String(chunk)); return true; });
  vi.spyOn(process.stderr, "write").mockImplementation(() => true);
  resetRateLimitsForTests();
});

afterEach(async () => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  await rm(logDir, { recursive: true, force: true });
});

async function fileRecords(): Promise<Record<string, unknown>[]> {
  const names = (await readdir(logDir)).filter((name) => name.endsWith(".jsonl"));
  const lines = await Promise.all(names.map((name) => readFile(path.join(logDir, name), "utf8")));
  return lines.flatMap((value) => value.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line) as Record<string, unknown>));
}

describe("BFF access log", () => {
  it("writes one allow-listed JSON record to stdout and file, with a response request ID", async () => {
    const request = new Request(`https://example.test/api/portal/vehicles/${vehicleId}/logs?vin=${vinShaped}`, {
      method: "POST", headers: { cookie: "portal_access_token=private-token" }, body: "private-password",
    });
    const handler = withAccessLog("/api/portal/vehicles/[vehicleId]/logs", async (request: Request, context: { params: Promise<{ vehicleId: string }> }) => {
      void request;
      void context;
      setAccessUserId(userId);
      return new Response("ok", { status: 201 });
    });
    const response = await handler(request, { params: Promise.resolve({ vehicleId }) });
    const records = await fileRecords();
    expect(records).toHaveLength(1);
    expect(records[0]).toEqual({
      timestamp_utc: expect.stringMatching(/^\d{4}-\d{2}-\d{2}T.*Z$/),
      request_id: response.headers.get("X-Request-ID"), user_id: userId, account: null,
      vehicle_id: vehicleId, method: "POST", route: "/api/portal/vehicles/[vehicleId]/logs", status: 201,
    });
    expect(stdout).toHaveLength(1);
    expect(JSON.parse(stdout[0])).toEqual(records[0]);
    expect(JSON.stringify(records) + stdout.join("")).not.toMatch(/private-token|private-password|IIIIIIIIIIIIIIIII|\?vin=/);
  });

  it("logs a safe account for failed login but suppresses VIN-shaped account input", async () => {
    vi.stubEnv("PORTAL_PUBLIC_ORIGIN", "https://example.test");
    vi.stubEnv("STF_V3_API_BASE_URL", "http://v3.internal:8002");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 400 })));
    for (const account of ["manager_1", vinShaped]) {
      const request = new Request("https://example.test/api/auth/login?private=token", {
        method: "POST",
        headers: { origin: "https://example.test", "content-type": "application/json" },
        body: JSON.stringify({ username: account, password: "private-password" }),
      });
      expect((await login(request)).status).toBe(401);
    }
    const records = await fileRecords();
    expect(records).toHaveLength(2);
    expect(records[0]).toMatchObject({ route: "/api/auth/login", account: "manager_1", user_id: null, status: 401 });
    expect(records[1]).toMatchObject({ route: "/api/auth/login", account: null, user_id: null, status: 401 });
    expect(JSON.stringify(records) + stdout.join("")).not.toMatch(/private-password|IIIIIIIIIIIIIIIII|\?private=|token/);
  });

  it("uses the verified user ID rather than the submitted account after successful login", async () => {
    vi.stubEnv("PORTAL_PUBLIC_ORIGIN", "https://example.test");
    vi.stubEnv("STF_V3_API_BASE_URL", "http://v3.internal:8002");
    const token = `header.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }))}.signature`;
    const user = { id: userId, username: "manager_1", memberships: [] };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(Response.json({ access_token: token, token_type: "bearer" }))
      .mockResolvedValueOnce(Response.json(user)));
    const request = new Request("https://example.test/api/auth/login", {
      method: "POST", headers: { origin: "https://example.test", "content-type": "application/json" },
      body: JSON.stringify({ username: "manager_1", password: "private-password" }),
    });
    expect((await login(request)).status).toBe(200);
    expect((await fileRecords())[0]).toMatchObject({ user_id: userId, account: null, status: 200 });
    expect(stdout.join("")).not.toMatch(/private-password|manager_1|signature/);
  });

  it("records early limits and thrown errors once, and leaves stream bodies untouched", async () => {
    const request = new Request("https://example.test/api/portal/events", { headers: { accept: "text/event-stream" } });
    const limited = withAccessLog("/api/portal/limited", async (request: Request) => { void request; return new Response(null, { status: 429 }); });
    expect((await limited(request)).status).toBe(429);
    const failed = withAccessLog("/api/portal/failed", async (request: Request): Promise<Response> => { void request; throw new Error("secret detail"); });
    const errorResponse = await failed(request);
    expect(errorResponse.status).toBe(500);
    expect(errorResponse.headers.get("X-Request-ID")).toBeTruthy();
    expect(await errorResponse.text()).not.toContain("secret detail");
    const stream = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode("data: ok\n\n")); controller.close(); } });
    const streamed = withAccessLog("/api/portal/events", async (request: Request) => { void request; return new Response(stream, { headers: { "Content-Type": "text/event-stream" } }); });
    const response = await streamed(request);
    expect(await response.text()).toBe("data: ok\n\n");
    const image = withAccessLog("/api/portal/manuals/[manualId]/assets/[...assetPath]", async (imageRequest: Request) => {
      void imageRequest;
      return new Response("PNG", { headers: { "Content-Type": "image/png" } });
    });
    const imageResponse = await image(request);
    expect(imageResponse.headers.get("Content-Type")).toBe("image/png");
    expect(imageResponse.headers.get("X-Request-ID")).toBeTruthy();
    expect(await imageResponse.text()).toBe("PNG");
    expect((await fileRecords()).map((record) => record.status)).toEqual([429, 500, 200, 200]);
    expect(stdout).toHaveLength(4);
    expect(stdout.join("")).not.toContain("secret detail");
  });

  it("compresses yesterday's file and removes records older than 30 UTC days", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-07T00:01:00.000Z"));
    const yesterday = path.join(logDir, "access-2026-11-06-999.jsonl");
    const expired = path.join(logDir, "access-2026-10-07-999.jsonl");
    await writeFile(yesterday, '{"status":200}\n');
    await writeFile(expired, '{"status":200}\n');
    const handler = withAccessLog("/api/health", async (request: Request) => { void request; return new Response(null, { status: 200 }); });
    await handler(new Request("https://example.test/api/health"));
    const names = await readdir(logDir);
    expect(names).toContain("access-2026-11-06-999.jsonl.gz");
    expect(names).not.toContain("access-2026-11-06-999.jsonl");
    expect(names).not.toContain("access-2026-10-07-999.jsonl");
    expect(gunzipSync(await readFile(yesterday + ".gz")).toString()).toBe('{"status":200}\n');
  });

  it("continues serving when the file sink fails after stdout succeeds", async () => {
    const blocker = path.join(logDir, "not-a-directory");
    await writeFile(blocker, "blocker");
    vi.stubEnv("PORTAL_ACCESS_LOG_DIR", blocker);
    const handler = withAccessLog("/api/health", async (request: Request) => { void request; return new Response(null, { status: 200 }); });
    expect((await handler(new Request("https://example.test/api/health"))).status).toBe(200);
    expect(stdout).toHaveLength(1);
    expect(vi.mocked(process.stderr.write)).toHaveBeenCalledWith(expect.stringContaining("access_log_write_failed"));
  });
});
