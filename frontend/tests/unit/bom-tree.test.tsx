import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BomTree } from "@/components/bom-tree";
import { LocaleProvider } from "@/components/locale-provider";
import type { BomNode } from "@/lib/domain";

const node = (code: string, parentCode: string | null): BomNode => ({
  code,
  parentCode,
  level: null,
  nameZh: code,
  nameEn: null,
  applicability: null,
  evidenceState: null,
  confidence: null,
});

describe("BomTree indentation", () => {
  it("indents every child level by two Chinese-character widths", () => {
    render(
      <LocaleProvider initialLocale="zh-HK">
        <BomTree
          nodes={[node("系統", null), node("總成", "系統"), node("部件", "總成")]}
          selected={null}
          onSelect={vi.fn()}
        />
      </LocaleProvider>,
    );

    const root = screen.getByRole("treeitem", { name: /系統/ });
    const assembly = screen.getByRole("treeitem", { name: /總成/ });
    expect(root).toHaveStyle({ "--tree-indent": "0em" });
    expect(assembly).toHaveStyle({ "--tree-indent": "2em" });

    fireEvent.keyDown(assembly.querySelector("button")!, { key: "ArrowRight" });
    expect(screen.getByRole("treeitem", { name: /部件/ })).toHaveStyle({ "--tree-indent": "4em" });
  });
});
