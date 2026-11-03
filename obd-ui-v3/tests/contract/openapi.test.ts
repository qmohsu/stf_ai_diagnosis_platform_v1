import fs, { type Dirent } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
const spec = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "..", "docs", "api", "v3_openapi.json"), "utf8"));
const requiredPaths = ["/v3/auth/login", "/v3/auth/logout", "/v3/users/me", "/v3/workshops/{workshop_id}/vehicles", "/v3/vehicles/{vehicle_id}", "/v3/vehicles/{vehicle_id}/logs", "/v3/vehicles/{vehicle_id}/diagnose", "/v3/vehicles/{vehicle_id}/conversations", "/v3/conversations/{conversation_id}", "/v3/conversations/{conversation_id}/events", "/v3/conversations/{conversation_id}/report", "/v3/manuals", "/v3/manuals/{manual_id}", "/v3/manuals/{manual_id}/toc", "/v3/manuals/search"];
describe("V3 contract boundary", () => {
  it("freezes current read endpoints and later Stage 1 paths", () => { for (const route of requiredPaths) expect(spec.paths, route).toHaveProperty(route); });
  it("keeps V3 domain schemas and vehicle semantics", () => { const schemas = spec.components.schemas; for (const name of ["VehicleOut", "ObdLogOut", "ConversationOut", "ReportOut", "ManualOut", "ManualSearchOut", "ManualTocOut"]) expect(schemas).toHaveProperty(name); const vehicle = schemas.VehicleOut.properties; for (const field of ["vin", "plate", "manufacturer", "model"]) expect(vehicle).toHaveProperty(field); });
  it("keeps username/password form login and Bearer user lookup", () => { expect(spec.paths["/v3/auth/login"].post.requestBody.content).toHaveProperty("application/x-www-form-urlencoded"); expect(spec.paths["/v3/users/me"].get.security).toContainEqual({ OAuth2PasswordBearer: [] }); });
  it("contains no legacy V1/V2 runtime calls", () => { const files = walk(path.resolve(process.cwd(), "src")); for (const file of files) expect(fs.readFileSync(file, "utf8"), file).not.toMatch(/\/v[12]\//); });
  it("contains no vehicle_data_twin runtime dependency", () => { const files = walk(path.resolve(process.cwd(), "src")); for (const file of files) expect(fs.readFileSync(file, "utf8"), file).not.toMatch(/vehicle_data_twin|VEHICLE_TWIN_API_BASE_URL|127\.0\.0\.1:4173/); });
});

if (process.env.E2E_LIVE === "1") {
  describe("live V3 OpenAPI smoke", () => {
    it("checks only the OpenAPI document and never protected data", async () => {
      const base = process.env.STF_V3_API_BASE_URL;
      if (!base) throw new Error("STF_V3_API_BASE_URL is required when E2E_LIVE=1");
      const response = await fetch(new URL("/v3/openapi.json", base));
      expect(response.ok).toBe(true);
      const live = await response.json() as { paths?: Record<string, unknown> };
      for (const route of requiredPaths) expect(live.paths).toHaveProperty(route);
    });
  });
}
function walk(root: string): string[] { const entries = fs.readdirSync(root, { withFileTypes: true }) as unknown as Dirent[]; return entries.flatMap((entry) => entry.isDirectory() ? walk(path.join(root, entry.name)) : /\.(ts|tsx|js)$/.test(entry.name) ? [path.join(root, entry.name)] : []); }
