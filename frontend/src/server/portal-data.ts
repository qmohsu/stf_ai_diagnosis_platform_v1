import type { IdentityType, VehicleCard, VehicleDirectoryResponse, VehicleHome } from "@/lib/domain";
import { isValidPlate, normalizePlate, normalizeVin } from "@/lib/vehicle";
import { PortalError } from "@/server/errors";
import { mapBom, mapComponentDetail, mapCoverage, mapEvents, mapIntervals, mapReadiness, mapVehicle, mapVehicles, settledState } from "@/server/mappers";
import { twinGet } from "@/server/twin";

export async function vehicleDirectory(query = ""): Promise<VehicleDirectoryResponse> {
  const vehicles = mapVehicles(await twinGet("/api/vehicles"));
  const needle = query.trim().toLocaleLowerCase();
  const filtered = needle ? vehicles.filter((vehicle) => [vehicle.plate, vehicle.vin, vehicle.manufacturer, vehicle.model, vehicle.modelFamily, vehicle.department].some((value) => value?.toLocaleLowerCase().includes(needle))) : vehicles;
  return {
    vehicles: filtered, total: vehicles.length, filtered: filtered.length,
    stats: filtered.reduce((totals, vehicle) => ({ maintenanceOrders: totals.maintenanceOrders + vehicle.counts.maintenanceOrders, events: totals.events + vehicle.counts.events, componentInstances: totals.componentInstances + vehicle.counts.componentInstances }), { maintenanceOrders: 0, events: 0, componentInstances: 0 }),
  };
}

export async function resolveVehicle(identityType: IdentityType, identity: string): Promise<VehicleCard> {
  if (identityType === "plate") {
    const plate = normalizePlate(identity);
    if (!isValidPlate(plate)) throw new PortalError(400, "INVALID_PLATE", "Plate is required and must match the vehicle platform format");
    return mapVehicle(await twinGet(`/api/vehicles/${encodeURIComponent(plate)}/profile`));
  }
  const vin = normalizeVin(identity);
  const match = mapVehicles(await twinGet("/api/vehicles")).find((vehicle) => vehicle.vin === vin);
  if (!match) throw new PortalError(404, "VIN_NOT_FOUND", "Vehicle VIN not found");
  return match;
}

export async function vehicleHome(identityType: IdentityType, identity: string): Promise<VehicleHome> {
  const vehicle = await resolveVehicle(identityType, identity);
  const plate = encodeURIComponent(vehicle.plate);
  const [bom, events, coverage, readiness, intervals] = await Promise.allSettled([
    twinGet(`/api/vehicles/${plate}/bom`).then(mapBom), twinGet(`/api/vehicles/${plate}/events`).then(mapEvents),
    twinGet(`/api/vehicles/${plate}/coverage`).then(mapCoverage), twinGet(`/api/vehicles/${plate}/survival-readiness`).then(mapReadiness),
    twinGet(`/api/vehicles/${plate}/survival-intervals`).then(mapIntervals),
  ]);
  const deferred = { kind: "deferred" } as const;
  return {
    vehicle,
    bom: settledState(bom, (value) => value.length === 0),
    events: settledState(events, (value) => value.length === 0),
    coverage: settledState(coverage, (value) => value.windows.length === 0 && value.gaps.length === 0),
    readiness: settledState(readiness, () => false),
    survivalIntervals: settledState(intervals, (value) => value.time.length === 0 && value.mileage.length === 0),
    v3: { logs: deferred, diagnosis: deferred, reports: deferred, manuals: deferred },
  };
}

export async function componentDetail(identityType: IdentityType, identity: string, componentCode: string) {
  const vehicle = await resolveVehicle(identityType, identity);
  const code = componentCode.trim();
  if (!code) throw new PortalError(400, "INVALID_COMPONENT", "Component code is required");
  const raw = await twinGet(`/api/vehicles/${encodeURIComponent(vehicle.plate)}/components/${encodeURIComponent(code)}`);
  return mapComponentDetail(raw, vehicle);
}
