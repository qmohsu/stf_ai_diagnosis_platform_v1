import type { VehicleCard, VehicleVerificationCandidate, VehicleVerificationSuggestion } from "@/lib/domain";
import { normalizePlate } from "@/lib/vehicle";
import { PortalError } from "@/server/errors";
import { mapVehicles } from "@/server/mappers";
import { twinGet } from "@/server/twin";

const EXCLUDED_CLASS = /(motorcycle|motor\s?bike|tricycle|three[- ]wheel|摩托|電單車|电单车|三輪|三轮)/i;
const ELECTRIC_POWERTRAIN = /^(ev|bev|battery electric|electric)$/i;

export interface VerificationInput {
  plate: string;
  vin: string;
  manufacturer: string;
  model: string;
}

export async function verificationVehicles(): Promise<VehicleCard[]> {
  return mapVehicles(await twinGet("/api/vehicles")).filter(isVerificationEligible);
}

export function isVerificationEligible(vehicle: VehicleCard): boolean {
  const classification = [vehicle.vehicleClass, vehicle.powertrainType, vehicle.fuelType].filter(Boolean).join(" ");
  if (EXCLUDED_CLASS.test(classification)) return false;
  return ![vehicle.powertrainType, vehicle.fuelType].some((value) => ELECTRIC_POWERTRAIN.test(value?.trim() ?? ""));
}

export async function plateSuggestions(query: string): Promise<VehicleVerificationSuggestion[]> {
  const needle = normalizePlate(query);
  if (!needle) return [];
  return (await verificationVehicles())
    .filter((vehicle) => vehicle.plate.includes(needle))
    .sort((left, right) => Number(right.plate.startsWith(needle)) - Number(left.plate.startsWith(needle)) || left.plate.localeCompare(right.plate))
    .slice(0, 8)
    .map((vehicle) => ({ plate: vehicle.plate, vinState: vehicle.vin ? "ready" : "pending_source" }));
}

export async function verificationCandidate(plateInput: string): Promise<VehicleVerificationCandidate> {
  const plate = normalizePlate(plateInput);
  const vehicle = (await verificationVehicles()).find((item) => item.plate === plate);
  if (!vehicle) throw new PortalError(404, "VEHICLE_NOT_FOUND", "Vehicle was not found in the supported fleet");
  return toCandidate(vehicle);
}

export async function brandSuggestions(query: string): Promise<string[]> {
  const needle = query.trim().toLocaleLowerCase();
  const brands = unique((await verificationVehicles()).map((vehicle) => vehicle.manufacturer));
  return brands
    .filter((brand) => !needle || brand.toLocaleLowerCase().includes(needle))
    .sort((left, right) => Number(right.toLocaleLowerCase().startsWith(needle)) - Number(left.toLocaleLowerCase().startsWith(needle)) || left.localeCompare(right))
    .slice(0, 8);
}

export async function modelsForBrand(brandInput: string): Promise<string[]> {
  const brand = brandInput.trim().toLocaleLowerCase();
  if (!brand) return [];
  return unique((await verificationVehicles())
    .filter((vehicle) => vehicle.manufacturer.trim().toLocaleLowerCase() === brand)
    .map((vehicle) => vehicle.model))
    .sort((left, right) => left.localeCompare(right));
}

export async function verifyVehicleIdentity(input: VerificationInput): Promise<VehicleCard> {
  const plate = normalizePlate(input.plate);
  const manufacturer = input.manufacturer.trim().toLocaleLowerCase();
  const vin = input.vin.trim();
  const model = input.model;
  if (!plate || !vin || !manufacturer || !model) throw verificationFailed();

  const vehicle = (await verificationVehicles()).find((item) => item.plate === plate);
  if (!vehicle) throw verificationFailed();
  if (!vehicle.vin) throw new PortalError(409, "VEHICLE_IDENTITY_INCOMPLETE", "Vehicle identity data is not yet complete");
  if (vehicle.vin !== vin || vehicle.manufacturer.trim().toLocaleLowerCase() !== manufacturer || vehicle.model !== model) throw verificationFailed();
  return vehicle;
}

function toCandidate(vehicle: VehicleCard): VehicleVerificationCandidate {
  return { plate: vehicle.plate, vin: vehicle.vin, manufacturer: vehicle.manufacturer, model: vehicle.model, verifiable: Boolean(vehicle.vin) };
}

function unique(values: string[]): string[] { return [...new Set(values)]; }
function verificationFailed(): PortalError { return new PortalError(422, "VEHICLE_VERIFICATION_FAILED", "Vehicle details could not be verified"); }
