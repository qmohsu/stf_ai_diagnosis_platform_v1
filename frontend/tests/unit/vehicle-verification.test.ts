import { afterEach, describe, expect, it, vi } from "vitest";
import type { VehicleCard } from "@/lib/domain";
import { decodeVehicleContext, encodeVehicleContext, matchesVehicleContext } from "@/server/vehicle-session";
import { brandSuggestions, isVerificationEligible, modelsForBrand, plateSuggestions, verificationCandidate, verifyVehicleIdentity } from "@/server/vehicle-verification";

const PRIMARY_TEST_VIN = "JHMGK5830HX202404";
const SECONDARY_TEST_VIN = "1HGCM82633A123456";

const profiles = [
  { plate: "UY8280", brand: "TOYOTA", model: "COASTER", vehicle_class: "Light Bus", fuel_type: "Diesel", powertrain_type: "ICE" },
  { plate: "AB1234", vin: PRIMARY_TEST_VIN, brand: "HONDA", model: "ACCORD", vehicle_class: "Saloon", fuel_type: "Petrol", powertrain_type: "ICE" },
  { plate: "VN531", vin: SECONDARY_TEST_VIN, brand: "Hino", model: "16T Lorry", vehicle_class: "Lorry", fuel_type: "Diesel", powertrain_type: "ICE" },
  { plate: "EV531", vin: SECONDARY_TEST_VIN, brand: "TEST", model: "ELECTRIC", vehicle_class: "Van", fuel_type: "Electric", powertrain_type: "EV" },
  { plate: "MC531", vin: SECONDARY_TEST_VIN, brand: "TEST", model: "BIKE", vehicle_class: "Motorcycle", fuel_type: "Petrol", powertrain_type: "ICE" },
];

afterEach(() => vi.unstubAllGlobals());

describe("vehicle verification data", () => {
  it("orders matching plates, excludes unsupported vehicle classes and limits results", async () => {
    stubProfiles([...profiles, ...Array.from({ length: 10 }, (_, index) => ({ plate: `${String.fromCharCode(65 + index)}531`, vin: SECONDARY_TEST_VIN, brand: "Hino", model: "16T Lorry", vehicle_class: "Lorry", fuel_type: "Diesel", powertrain_type: "ICE" }))]);
    const matches = await plateSuggestions("531");
    expect(matches).toHaveLength(8);
    expect(matches.map((item) => item.plate)).not.toContain("EV531");
    expect(matches.map((item) => item.plate)).not.toContain("MC531");
  });

  it("puts prefix matches before other contains matches", async () => {
    stubProfiles([...profiles, { plate: "AVN531", vin: SECONDARY_TEST_VIN, brand: "Hino", model: "500 Series Lorry", vehicle_class: "Lorry", fuel_type: "Diesel", powertrain_type: "ICE" }]);
    await expect(plateSuggestions("vn")).resolves.toEqual([
      { plate: "VN531", vinState: "ready" },
      { plate: "AVN531", vinState: "ready" },
    ]);
  });

  it("returns canonical candidate data and standard brand/model options", async () => {
    stubProfiles(profiles);
    await expect(verificationCandidate(" vn-531 ")).resolves.toMatchObject({ plate: "VN531", manufacturer: "Hino", model: "16T Lorry", verifiable: true });
    await expect(brandSuggestions("hi")).resolves.toEqual(["Hino"]);
    await expect(modelsForBrand("hInO")).resolves.toEqual(["16T Lorry"]);
  });

  it("uses case-insensitive plate/brand but exact trimmed VIN and model", async () => {
    stubProfiles(profiles);
    await expect(verifyVehicleIdentity({ plate: " ab-1234 ", vin: PRIMARY_TEST_VIN, manufacturer: "honda", model: "ACCORD" })).resolves.toMatchObject({ plate: "AB1234" });
    await expect(verifyVehicleIdentity({ plate: "AB1234", vin: PRIMARY_TEST_VIN.toLowerCase(), manufacturer: "HONDA", model: "ACCORD" })).rejects.toMatchObject({ status: 422, code: "VEHICLE_VERIFICATION_FAILED" });
    await expect(verifyVehicleIdentity({ plate: "AB1234", vin: PRIMARY_TEST_VIN, manufacturer: "HONDA", model: "16T Lorry" })).rejects.toMatchObject({ status: 422, code: "VEHICLE_VERIFICATION_FAILED" });
  });

  it("keeps a missing-VIN vehicle visible but blocks verification", async () => {
    stubProfiles(profiles);
    await expect(verificationCandidate("UY8280")).resolves.toMatchObject({ vin: null, verifiable: false });
    await expect(verifyVehicleIdentity({ plate: "UY8280", vin: "-", manufacturer: "TOYOTA", model: "COASTER" })).rejects.toMatchObject({ status: 409, code: "VEHICLE_IDENTITY_INCOMPLETE" });
  });

  it("recognizes explicitly excluded vehicles", () => {
    expect(isVerificationEligible(card({ powertrainType: "EV" }))).toBe(false);
    expect(isVerificationEligible(card({ vehicleClass: "Motorcycle" }))).toBe(false);
    expect(isVerificationEligible(card({ vehicleClass: "Light Bus", powertrainType: "ICE" }))).toBe(true);
  });
});

describe("signed vehicle context", () => {
  it("round-trips one verified vehicle and rejects tampering or another vehicle", async () => {
    const vehicle = card({ plate: "AB1234", vin: PRIMARY_TEST_VIN, canonicalIdentityType: "vin", canonicalIdentity: PRIMARY_TEST_VIN, canonicalPath: `/vehicles/vin/${PRIMARY_TEST_VIN}` });
    const encoded = await encodeVehicleContext(vehicle);
    const decoded = await decodeVehicleContext(encoded);
    expect(decoded).toMatchObject({ plate: "AB1234", vin: PRIMARY_TEST_VIN });
    expect(decoded && matchesVehicleContext(decoded, "plate", "ab-1234")).toBe(true);
    expect(decoded && matchesVehicleContext(decoded, "vin", PRIMARY_TEST_VIN)).toBe(true);
    expect(decoded && matchesVehicleContext(decoded, "plate", "VN531")).toBe(false);
    await expect(decodeVehicleContext(`${encoded.slice(0, -1)}x`)).resolves.toBeNull();
  });
});

function stubProfiles(value: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } })));
}

function card(overrides: Partial<VehicleCard>): VehicleCard {
  return {
    identityState: "vin", canonicalIdentityType: "vin", canonicalIdentity: PRIMARY_TEST_VIN, canonicalPath: `/vehicles/vin/${PRIMARY_TEST_VIN}`,
    vin: PRIMARY_TEST_VIN, plate: "AB1234", manufacturer: "HONDA", model: "ACCORD", modelFamily: null, vehicleClass: "Saloon", fuelType: "Petrol", powertrainType: "ICE", parkingLocation: null, department: null,
    counts: { maintenanceOrders: 0, events: 0, componentInstances: 0, qualityIssues: 0 },
    ...overrides,
  };
}
