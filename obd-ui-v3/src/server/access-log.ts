import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { appendFile, chmod, mkdir, readdir, rename, stat, unlink } from "node:fs/promises";
import path from "node:path";
import { pipeline } from "node:stream/promises";
import { createGzip } from "node:zlib";
import { portalError } from "@/server/http";

type AccessContext = {
  requestId: string;
  userId: string | null;
  account: string | null;
  vehicleId: string | null;
};

type RouteContext = { params?: Promise<Record<string, unknown>> };

const accessContext = new AsyncLocalStorage<AccessContext>();
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SAFE_USER_ID = /^[A-Za-z0-9._:-]{1,64}$/;
const SAFE_ACCOUNT = /^[A-Za-z0-9._-]{3,50}$/;
const VIN_SHAPED = /[A-Za-z0-9]{17}/;
const LOG_FILE = /^access-(\d{4}-\d{2}-\d{2})-(\d+)\.jsonl(\.gz)?$/;
const DAY_MS = 86_400_000;
const KEEP_DAYS = 30;
const MAX_BYTES = 2 * 1024 * 1024 * 1024;

let fileQueue: Promise<void> = Promise.resolve();
let lastMaintenanceKey = "";
let writesSinceMaintenance = 0;

/** Only stable identifiers from validated server data enter the log. */
export function setAccessUserId(id: string): void {
  const context = accessContext.getStore();
  if (context && SAFE_USER_ID.test(id) && !VIN_SHAPED.test(id)) {
    context.userId = id;
    context.account = null;
  }
}

/** For failed logins only; VIN-shaped or unvalidated account strings stay out. */
export function setAccessAccount(account: string): void {
  const context = accessContext.getStore();
  if (context && !context.userId && SAFE_ACCOUNT.test(account) && !VIN_SHAPED.test(account)) {
    context.account = account;
  }
}

export function setAccessVehicleId(id: string): void {
  const context = accessContext.getStore();
  if (context && UUID.test(id)) context.vehicleId = id;
}

/** Wrap every BFF route so early limits, errors, images and SSE are covered. */
export function withAccessLog<Args extends unknown[], Result extends Response>(
  route: string,
  handler: (...args: Args) => Promise<Result>,
): (...args: Args) => Promise<Result> {
  return async (...args) => {
    const request = args[0] instanceof Request ? args[0] : null;
    const state: AccessContext = { requestId: randomUUID(), userId: null, account: null, vehicleId: null };
    return accessContext.run(state, async () => {
      const vehicleId = await vehicleFromRouteContext(args[1]);
      if (vehicleId) setAccessVehicleId(vehicleId);
      try {
        const response = await handler(...args);
        response.headers.set("X-Request-ID", state.requestId);
        await writeAccessRecord(state, request?.method ?? "GET", route, response.status);
        return response;
      } catch (error) {
        const response = portalError(error) as unknown as Result;
        response.headers.set("X-Request-ID", state.requestId);
        await writeAccessRecord(state, request?.method ?? "GET", route, response.status);
        return response;
      }
    });
  };
}

async function vehicleFromRouteContext(context: unknown): Promise<string | null> {
  if (!context || typeof context !== "object" || !("params" in context)) return null;
  try {
    const params = await (context as RouteContext).params;
    const candidate = params?.vehicleId;
    return typeof candidate === "string" && UUID.test(candidate) ? candidate : null;
  } catch { return null; }
}

async function writeAccessRecord(state: AccessContext, method: string, route: string, status: number): Promise<void> {
  const now = new Date();
  const record = {
    timestamp_utc: now.toISOString(),
    request_id: state.requestId,
    user_id: state.userId,
    account: state.userId ? null : state.account,
    vehicle_id: state.vehicleId,
    method,
    route,
    status,
  };
  const line = JSON.stringify(record) + "\n";
  try { process.stdout.write(line); }
  catch { reportSinkFailure(state.requestId, "stdout"); }

  // Serialize writes within this process, including daily rotation and pruning.
  const write = fileQueue.then(() => appendAccessLine(now, line));
  fileQueue = write.catch(() => undefined);
  try { await write; }
  catch { reportSinkFailure(state.requestId, "file"); }
}

function reportSinkFailure(requestId: string, sink: "stdout" | "file"): void {
  try { process.stderr.write(JSON.stringify({ event: "access_log_write_failed", request_id: requestId, sink }) + "\n"); }
  catch { /* Logging must never change the API result. */ }
}

async function appendAccessLine(now: Date, line: string): Promise<void> {
  const dir = process.env.PORTAL_ACCESS_LOG_DIR?.trim() || path.join(process.cwd(), ".portal-logs");
  const day = now.toISOString().slice(0, 10);
  await mkdir(dir, { recursive: true, mode: 0o700 });
  await chmod(dir, 0o700);
  const maintenanceKey = `${dir}|${day}`;
  if (maintenanceKey !== lastMaintenanceKey || ++writesSinceMaintenance >= 256) {
    await rotateAndPrune(dir, now);
    lastMaintenanceKey = maintenanceKey;
    writesSinceMaintenance = 0;
  }
  const currentFile = path.join(dir, `access-${day}-${process.pid}.jsonl`);
  await appendFile(currentFile, line, { encoding: "utf8", mode: 0o600 });
  await chmod(currentFile, 0o600);
}

async function rotateAndPrune(dir: string, now: Date): Promise<void> {
  const today = Date.parse(now.toISOString().slice(0, 10) + "T00:00:00.000Z");
  for (const name of await readdir(dir)) {
    const match = LOG_FILE.exec(name);
    if (!match) continue;
    const fileDay = Date.parse(match[1] + "T00:00:00.000Z");
    if (!Number.isFinite(fileDay)) continue;
    const file = path.join(dir, name);
    if (today - fileDay >= KEEP_DAYS * DAY_MS) {
      await unlink(file);
    } else if (!match[3] && fileDay < today) {
      const compressed = file + ".gz";
      const temporary = compressed + ".tmp";
      try {
        await pipeline(createReadStream(file), createGzip(), createWriteStream(temporary, { mode: 0o600 }));
        await rename(temporary, compressed);
        await unlink(file);
      } catch (error) {
        await unlink(temporary).catch(() => undefined);
        throw error;
      }
    }
  }
  const archived: { file: string; age: number; size: number }[] = [];
  let total = 0;
  for (const name of await readdir(dir)) {
    const match = LOG_FILE.exec(name);
    if (!match) continue;
    const file = path.join(dir, name);
    const size = (await stat(file)).size;
    total += size;
    if (match[3]) archived.push({ file, age: Date.parse(match[1]), size });
  }
  archived.sort((a, b) => a.age - b.age);
  for (const old of archived) {
    if (total <= MAX_BYTES) break;
    await unlink(old.file);
    total -= old.size;
  }
}
