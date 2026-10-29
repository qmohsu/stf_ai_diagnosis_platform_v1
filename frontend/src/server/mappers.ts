import type {
  BomNode, ComponentDetail, ComponentInstance, CoverageGap, CoverageWindow, DataState,
  LifecycleTransition, MaintenanceOrder, PartUsage, PortalEvent, QualityIssue,
  SurvivalInterval, SurvivalReadiness, VehicleCard,
} from "@/lib/domain";
import { isValidPlate, isValidVin, normalizePlate, normalizeVin, vehiclePath } from "@/lib/vehicle";
import { PortalError, errorId } from "@/server/errors";

type Row = Record<string, unknown>;

function row(value: unknown, label: string): Row {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PortalError(502, "TWIN_CONTRACT", `Vehicle platform ${label} is invalid`);
  }
  return value as Row;
}

function rows(value: unknown, label: string): Row[] {
  if (!Array.isArray(value)) throw new PortalError(502, "TWIN_CONTRACT", `Vehicle platform ${label} is invalid`);
  return value.map((item) => row(item, label));
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function requiredText(value: unknown, field: string): string {
  const result = text(value);
  if (!result) throw new PortalError(502, "TWIN_CONTRACT", `Vehicle platform is missing ${field}`);
  return result;
}

function number(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "bigint") return Number(value);
  return null;
}
function scalarText(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function count(value: unknown): number { return Math.max(0, Math.trunc(number(value) ?? 0)); }
function id(value: unknown): number | string { return number(value) ?? text(value) ?? "-"; }

export function mapVehicle(value: unknown): VehicleCard {
  const source = row(value, "vehicle profile");
  const plate = normalizePlate(requiredText(source.plate, "plate"));
  if (!isValidPlate(plate)) throw new PortalError(502, "TWIN_INVALID_PLATE", "Vehicle platform returned an invalid plate");
  const manufacturer = requiredText(source.manufacturer ?? source.brand, "manufacturer/brand");
  const model = requiredText(source.model, "model");
  const sourceVin = text(source.vin);
  if (sourceVin && !isValidVin(sourceVin)) throw new PortalError(502, "TWIN_INVALID_VIN", "Vehicle platform returned an invalid VIN");
  const vin = sourceVin ? normalizeVin(sourceVin) : null;
  const canonicalIdentityType = vin ? "vin" : "plate";
  const canonicalIdentity = vin ?? plate;
  return {
    identityState: vin ? "vin" : "pending_source",
    canonicalIdentityType,
    canonicalIdentity,
    canonicalPath: vehiclePath(canonicalIdentityType, canonicalIdentity),
    vin, plate, manufacturer, model,
    modelFamily: text(source.model_family),
    vehicleClass: text(source.vehicle_class),
    fuelType: text(source.fuel_type),
    powertrainType: text(source.powertrain_type),
    parkingLocation: text(source.parking_location),
    department: text(source.section),
    counts: {
      maintenanceOrders: count(source.orders), events: count(source.events),
      componentInstances: count(source.instances), qualityIssues: count(source.quality_issues),
    },
  };
}

export function mapVehicles(value: unknown): VehicleCard[] { return rows(value, "vehicle directory").map(mapVehicle); }

export function mapBom(value: unknown): BomNode[] {
  return rows(value, "BOM").map((source) => ({
    code: requiredText(source.code, "BOM code"), parentCode: text(source.parent_code), level: number(source.level),
    nameZh: requiredText(source.name_zh ?? source.name_en ?? source.code, "BOM name"), nameEn: text(source.name_en),
    applicability: text(source.applicability), evidenceState: text(source.evidence_state), confidence: scalarText(source.confidence),
  }));
}

export function mapEvents(value: unknown): PortalEvent[] {
  return rows(value, "events").map((source) => ({
    id: id(source.id), orderNo: text(source.order_no), type: requiredText(source.event_type, "event_type"), subtype: text(source.event_subtype),
    occurredAt: text(source.occurred_at), occurredAtUpperBound: text(source.occurred_at_upper_bound), timeSemantics: text(source.time_semantics),
    datePrecision: text(source.date_precision), odometer: number(source.odometer), odometerUpperBound: number(source.odometer_upper_bound),
    systemCode: text(source.system_code), componentCode: text(source.component_code), descriptionZh: text(source.description_zh ?? source.description_source),
    descriptionEn: text(source.description_en), lifecycleEffect: text(source.lifecycle_effect), failureMode: text(source.failure_mode), confidence: scalarText(source.confidence),
  }));
}

function mapCoverageWindows(value: unknown): CoverageWindow[] {
  return rows(value, "coverage windows").map((source) => ({ id: id(source.id), startDate: text(source.start_date), endDate: text(source.end_date), startMileage: number(source.start_mileage), endMileage: number(source.end_mileage), coverageStatus: text(source.coverage_status), coverageType: text(source.coverage_type), confidence: scalarText(source.confidence), note: text(source.note) }));
}
function mapCoverageGaps(value: unknown): CoverageGap[] {
  return rows(value, "coverage gaps").map((source) => ({ id: id(source.id), startDate: text(source.start_date), endDate: text(source.end_date), gapType: text(source.gap_type ?? source.reason_code), description: text(source.description ?? source.note), gapDays: number(source.gap_days), confidence: scalarText(source.confidence) }));
}
export function mapCoverage(value: unknown) {
  const source = row(value, "coverage");
  return { windows: mapCoverageWindows(source.windows), gaps: mapCoverageGaps(source.gaps) };
}

export function mapReadiness(value: unknown): SurvivalReadiness {
  const source = row(value, "survival readiness");
  return { instances: count(source.instances), intervals: count(source.intervals), observedFailures: count(source.observed_failures), leftTruncated: count(source.left_truncated), excluded: count(source.excluded), coverageGaps: count(source.coverage_gaps) };
}

function mapIntervalRows(value: unknown, axis: "time" | "mileage"): SurvivalInterval[] {
  return rows(value, `${axis} survival intervals`).map((source) => ({
    componentCode: text(source.component_code), episode: number(source.episode),
    entry: axis === "time" ? text(source.entry_date) : number(source.entry_mileage),
    exit: axis === "time" ? text(source.exit_date) : number(source.exit_mileage),
    eventObserved: typeof source.event_observed === "boolean" ? source.event_observed : number(source.event_observed) === null ? null : number(source.event_observed) === 1,
    inclusionStatus: text(source.inclusion_status), truncationType: text(source.truncation_type),
  }));
}
export function mapIntervals(value: unknown) {
  const source = row(value, "survival intervals");
  return { time: mapIntervalRows(source.time, "time"), mileage: mapIntervalRows(source.mileage, "mileage") };
}

export function settledState<T>(result: PromiseSettledResult<T>, empty: (value: T) => boolean): DataState<T> {
  if (result.status === "rejected") {
    const reason = result.reason;
    const known = reason instanceof PortalError ? reason : new PortalError(502, "TWIN_SECTION", "Vehicle data section failed");
    return { kind: "error", message: known.message, errorId: errorId(known.code) };
  }
  return empty(result.value) ? { kind: "empty" } : { kind: "ready", value: result.value };
}

function mapOrders(value: unknown): MaintenanceOrder[] {
  return rows(value, "maintenance orders").map((source) => ({ orderNo: requiredText(source.order_no, "order_no"), status: text(source.status), statusLayer: text(source.status_layer), submittedAt: text(source.submitted_at), submitMileage: number(source.submit_mileage), orderTypeCode: text(source.order_type_code), orderTypeNameZh: text(source.order_type_name_zh), remarks: text(source.remarks) }));
}
function mapParts(value: unknown): PartUsage[] {
  return rows(value, "parts").map((source) => ({ id: id(source.id), orderNo: text(source.order_no), componentCode: text(source.component_code), partId: text(source.part_id), partNo: text(source.part_no ?? source.part_number), partName: text(source.part_name ?? source.name_zh ?? source.item_name), quantity: number(source.quantity), installationState: text(source.installation_state), confidence: scalarText(source.confidence) }));
}
function mapInstances(value: unknown): ComponentInstance[] {
  return rows(value, "component instances").map((source) => ({ id: id(source.id), componentCode: requiredText(source.component_code, "component_code"), generation: number(source.generation), partId: text(source.part_id), quantity: number(source.quantity), position: text(source.position), installedAt: text(source.installed_at ?? source.observed_from), installedMileage: number(source.installed_mileage ?? source.observed_from_mileage), removedAt: text(source.removed_at), removedMileage: number(source.removed_mileage), currentState: text(source.current_state), confidence: scalarText(source.confidence) }));
}
function mapTransitions(value: unknown): LifecycleTransition[] {
  return rows(value, "lifecycle transitions").map((source) => ({ id: id(source.id), generation: number(source.generation), eventType: text(source.event_type), eventSubtype: text(source.event_subtype), occurredAt: text(source.occurred_at), occurredAtUpperBound: text(source.occurred_at_upper_bound), fromState: text(source.from_state), toState: text(source.to_state), transitionType: text(source.transition_type), reason: text(source.reason), odometer: number(source.odometer), confidence: scalarText(source.confidence) }));
}
function mapQuality(value: unknown): QualityIssue[] {
  return rows(value, "quality issues").map((source) => ({ id: id(source.id), severity: text(source.severity), issueType: text(source.issue_type), message: text(source.message_zh ?? source.message ?? source.description), componentCode: text(source.component_code) }));
}

export function mapComponentDetail(value: unknown, vehicle: VehicleCard): ComponentDetail {
  const source = row(value, "component detail");
  const componentRow = row(source.bom, "component BOM");
  const component = mapBom([componentRow])[0];
  const summarySource = row(source.summary, "component summary");
  const summaryKeys = ["events", "faults", "actions", "part_events", "orders", "parts", "instances", "transitions", "intervals", "quality_issues"] as const;
  const summary = Object.fromEntries(summaryKeys.map((key) => [key, count(summarySource[key])]));
  const events = mapEvents(source.events), orders = mapOrders(source.orders), parts = mapParts(source.parts), instances = mapInstances(source.instances), transitions = mapTransitions(source.transitions), quality = mapQuality(source.quality);
  const intervals = { time: mapIntervalRows(source.time_intervals, "time"), mileage: mapIntervalRows(source.mileage_intervals, "mileage") };
  const coverage = mapCoverage(source.coverage);
  const state = <T,>(items: T[]): DataState<T[]> => items.length ? { kind: "ready", value: items } : { kind: "empty" };
  return {
    vehicle, component: { ...component, childCount: count(componentRow.child_count) }, ancestry: mapBom(source.ancestry), summary,
    events: state(events), orders: state(orders), parts: state(parts), instances: state(instances), transitions: state(transitions), quality: state(quality),
    intervals: intervals.time.length || intervals.mileage.length ? { kind: "ready", value: intervals } : { kind: "empty" },
    coverage: coverage.windows.length || coverage.gaps.length ? { kind: "ready", value: coverage } : { kind: "empty" },
  };
}
