export type Locale = "zh-HK" | "zh-CN";
export type IdentityType = "plate" | "vin";

export type DataState<T> =
  | { kind: "ready"; value: T }
  | { kind: "empty" }
  | { kind: "pendingSource" }
  | { kind: "deferred" }
  | { kind: "error"; message: string; errorId: string };

export interface VehicleIdentity {
  identityState: "vin" | "pending_source";
  canonicalIdentityType: IdentityType;
  canonicalIdentity: string;
  canonicalPath: string;
  vin: string | null;
  plate: string;
  manufacturer: string;
  model: string;
}

export interface VehicleCard extends VehicleIdentity {
  modelFamily: string | null;
  vehicleClass: string | null;
  fuelType: string | null;
  powertrainType: string | null;
  parkingLocation: string | null;
  department: string | null;
  counts: {
    maintenanceOrders: number;
    events: number;
    componentInstances: number;
    qualityIssues: number;
  };
}

export interface BomNode {
  code: string;
  parentCode: string | null;
  level: number | null;
  nameZh: string;
  nameEn: string | null;
  applicability: string | null;
  evidenceState: string | null;
  confidence: string | null;
}

export interface PortalEvent {
  id: number | string;
  orderNo: string | null;
  type: string;
  subtype: string | null;
  occurredAt: string | null;
  occurredAtUpperBound: string | null;
  timeSemantics: string | null;
  datePrecision: string | null;
  odometer: number | null;
  odometerUpperBound: number | null;
  systemCode: string | null;
  componentCode: string | null;
  descriptionZh: string | null;
  descriptionEn: string | null;
  lifecycleEffect: string | null;
  failureMode: string | null;
  confidence: string | null;
}

export interface CoverageWindow {
  id: number | string;
  startDate: string | null;
  endDate: string | null;
  startMileage: number | null;
  endMileage: number | null;
  coverageStatus: string | null;
  coverageType: string | null;
  confidence: string | null;
  note: string | null;
}

export interface CoverageGap {
  id: number | string;
  startDate: string | null;
  endDate: string | null;
  gapType: string | null;
  description: string | null;
  gapDays: number | null;
  confidence: string | null;
}

export interface SurvivalReadiness {
  instances: number;
  intervals: number;
  observedFailures: number;
  leftTruncated: number;
  excluded: number;
  coverageGaps: number;
}

export interface SurvivalInterval {
  componentCode: string | null;
  episode: number | null;
  entry: string | number | null;
  exit: string | number | null;
  eventObserved: boolean | null;
  inclusionStatus: string | null;
  truncationType: string | null;
}

export interface VehicleHome {
  vehicle: VehicleCard;
  bom: DataState<BomNode[]>;
  events: DataState<PortalEvent[]>;
  coverage: DataState<{ windows: CoverageWindow[]; gaps: CoverageGap[] }>;
  readiness: DataState<SurvivalReadiness>;
  survivalIntervals: DataState<{ time: SurvivalInterval[]; mileage: SurvivalInterval[] }>;
  v3: {
    logs: DataState<never>;
    diagnosis: DataState<never>;
    reports: DataState<never>;
    manuals: DataState<never>;
  };
}

export interface MaintenanceOrder {
  orderNo: string;
  status: string | null;
  statusLayer: string | null;
  submittedAt: string | null;
  submitMileage: number | null;
  orderTypeCode: string | null;
  orderTypeNameZh: string | null;
  remarks: string | null;
}

export interface PartUsage {
  id: number | string;
  orderNo: string | null;
  componentCode: string | null;
  partId: string | null;
  partNo: string | null;
  partName: string | null;
  quantity: number | null;
  installationState: string | null;
  confidence: string | null;
}

export interface ComponentInstance {
  id: number | string;
  componentCode: string;
  generation: number | null;
  partId: string | null;
  quantity: number | null;
  position: string | null;
  installedAt: string | null;
  installedMileage: number | null;
  removedAt: string | null;
  removedMileage: number | null;
  currentState: string | null;
  confidence: string | null;
}

export interface LifecycleTransition {
  id: number | string;
  generation: number | null;
  eventType: string | null;
  eventSubtype: string | null;
  occurredAt: string | null;
  occurredAtUpperBound: string | null;
  fromState: string | null;
  toState: string | null;
  transitionType: string | null;
  reason: string | null;
  odometer: number | null;
  confidence: string | null;
}

export interface QualityIssue {
  id: number | string;
  severity: string | null;
  issueType: string | null;
  message: string | null;
  componentCode: string | null;
}

export interface ComponentDetail {
  vehicle: VehicleCard;
  component: BomNode & { childCount: number };
  ancestry: BomNode[];
  summary: Record<string, number>;
  events: DataState<PortalEvent[]>;
  orders: DataState<MaintenanceOrder[]>;
  parts: DataState<PartUsage[]>;
  instances: DataState<ComponentInstance[]>;
  transitions: DataState<LifecycleTransition[]>;
  intervals: DataState<{ time: SurvivalInterval[]; mileage: SurvivalInterval[] }>;
  coverage: DataState<{ windows: CoverageWindow[]; gaps: CoverageGap[] }>;
  quality: DataState<QualityIssue[]>;
}

export interface VehicleDirectoryResponse {
  vehicles: VehicleCard[];
  total: number;
  filtered: number;
  stats: {
    maintenanceOrders: number;
    events: number;
    componentInstances: number;
  };
}

export interface VehicleVerificationSuggestion {
  plate: string;
  vinState: "ready" | "pending_source";
}

export interface VehicleVerificationCandidate {
  plate: string;
  vin: string | null;
  manufacturer: string;
  model: string;
  verifiable: boolean;
}

export interface VehicleVerificationContext {
  plate: string;
  vin: string;
  manufacturer: string;
  model: string;
  canonicalPath: string;
}
