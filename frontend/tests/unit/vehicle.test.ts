import { describe, expect, it } from "vitest";
import { buildBomTree, isValidPlate, isValidVin, normalizePlate, vehiclePath } from "@/lib/vehicle";
import type { BomNode } from "@/lib/domain";
const node = (code: string, parentCode: string | null): BomNode => ({ code, parentCode, level: null, nameZh: code, nameEn: null, applicability: null, evidenceState: null, confidence: null });
describe("vehicle identity", () => {
  it("requires and normalizes a vehicle-platform plate without treating it as VIN", () => { expect(normalizePlate(" uy-8280 ")).toBe("UY8280"); expect(isValidPlate("UY8280")).toBe(true); expect(isValidPlate(" ")).toBe(false); expect(isValidPlate("NOT-A-PLATE")).toBe(false); expect(vehiclePath("plate", "uy-8280")).toBe("/vehicles/plate/UY8280"); });
  it("accepts only V3-compatible 17-character VINs", () => { expect(isValidVin("JHMGK5830HX202404")).toBe(true); expect(isValidVin("JHMGK5830HX20240I")).toBe(false); expect(isValidVin("UY8280")).toBe(false); });
  it("builds a safe tree, derives depth, and promotes orphan/cycle nodes", () => { const tree = buildBomTree([node("A", null), node("B", "A"), node("C", "B"), node("ORPHAN", "X"), node("C1", "C2"), node("C2", "C1")]); const root = tree.find((item) => item.code === "A"); expect(root?.level).toBe(0); expect(root?.children[0].level).toBe(1); expect(root?.children[0].children[0].level).toBe(2); expect(tree.find((item) => item.code === "ORPHAN")?.orphaned).toBe(true); expect(tree.find((item) => item.code === "ORPHAN")?.level).toBe(0); expect(tree.some((item) => item.code === "C1" || item.code === "C2")).toBe(true); });
});
