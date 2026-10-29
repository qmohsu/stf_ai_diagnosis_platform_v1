import { describe, expect, it } from "vitest";
import { dictionaries } from "@/lib/portal-i18n";
function keys(value: unknown, prefix = ""): string[] { if (typeof value !== "object" || value === null) return [prefix]; return Object.entries(value).flatMap(([key, child]) => keys(child, prefix ? `${prefix}.${key}` : key)); }
describe("translations", () => { it("keeps Traditional and Simplified keys identical", () => { expect(keys(dictionaries["zh-CN"]).sort()).toEqual(keys(dictionaries["zh-HK"]).sort()); }); });
