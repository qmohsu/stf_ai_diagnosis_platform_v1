import { describe, expect, it } from "vitest";
import type { VehicleHome } from "@/lib/domain";
import { formatLatestVehicleData, latestVehicleData } from "@/lib/vehicle-dashboard";

function homeStates(overrides: Partial<Pick<VehicleHome, "events" | "coverage" | "survivalIntervals">> = {}): Pick<VehicleHome, "events" | "coverage" | "survivalIntervals"> {
  return {
    events: { kind: "empty" },
    coverage: { kind: "empty" },
    survivalIntervals: { kind: "empty" },
    ...overrides,
  };
}

describe("vehicle dashboard data", () => {
  it("selects the latest valid date without treating missing sections as data", () => {
    const result = latestVehicleData(homeStates({
      events: { kind: "ready", value: [{ id: 1, orderNo: null, type: "fault", subtype: null, occurredAt: "2026-08-10", occurredAtUpperBound: null, timeSemantics: null, datePrecision: "day", odometer: null, odometerUpperBound: null, systemCode: null, componentCode: null, descriptionZh: null, descriptionEn: null, lifecycleEffect: null, failureMode: null, confidence: null }] },
      coverage: { kind: "ready", value: { windows: [{ id: 1, startDate: "2024-01-01", endDate: "2026-09-01", startMileage: null, endMileage: null, coverageStatus: null, coverageType: null, confidence: null, note: null }], gaps: [] } },
      survivalIntervals: { kind: "ready", value: { time: [{ componentCode: null, episode: 1, entry: "2024-01-01", exit: "bad-date", eventObserved: null, inclusionStatus: null, truncationType: null }], mileage: [] } },
    }));
    expect(result).toBe("2026-09-01");
  });

  it("keeps date-only values absolute and formats recent timestamps relatively", () => {
    expect(formatLatestVehicleData("2026-08-10", "zh-CN", Date.parse("2026-08-10T12:00:00Z"))).toContain("2026");
    expect(formatLatestVehicleData("2026-10-20T10:55:00Z", "zh-CN", Date.parse("2026-10-20T11:00:00Z"))).toBe("5分钟前");
    expect(formatLatestVehicleData(null, "zh-HK")).toBe("-");
    expect(formatLatestVehicleData("not-a-date", "zh-HK")).toBe("-");
  });
});
