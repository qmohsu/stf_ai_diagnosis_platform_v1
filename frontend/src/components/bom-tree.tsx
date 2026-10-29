"use client";
import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { BomNode } from "@/lib/domain";
import { buildBomTree, type BomTreeNode } from "@/lib/vehicle";
import { useLocale } from "@/components/locale-provider";

export function BomTree({ nodes, selected, onSelect }: { nodes: BomNode[]; selected: string | null; onSelect: (code: string | null) => void }) {
  const tree = useMemo(() => buildBomTree(nodes), [nodes]);
  const { messages } = useLocale();
  const [expanded, setExpanded] = useState(() => new Set(tree.filter((node) => node.level === 0 || node.parentCode === null).map((node) => node.code)));
  const visible = useMemo(() => flattenVisible(tree, expanded), [expanded, tree]);
  const focus = (code: string) => document.querySelector<HTMLButtonElement>(`[data-tree-code="${CSS.escape(code)}"]`)?.focus();
  const keyDown = (event: React.KeyboardEvent<HTMLButtonElement>, node: BomTreeNode, index: number) => {
    if (["ArrowDown", "ArrowUp", "Home", "End", "ArrowRight", "ArrowLeft", "Enter", " "].includes(event.key)) event.preventDefault();
    if (event.key === "ArrowDown") focus(visible[Math.min(index + 1, visible.length - 1)].code);
    if (event.key === "ArrowUp") focus(visible[Math.max(index - 1, 0)].code);
    if (event.key === "Home") focus(visible[0].code);
    if (event.key === "End") focus(visible[visible.length - 1].code);
    if (event.key === "ArrowRight" && node.children.length) { if (!expanded.has(node.code)) toggle(node.code); else focus(node.children[0].code); }
    if (event.key === "ArrowLeft") { if (expanded.has(node.code)) toggle(node.code); else if (node.parentCode) focus(node.parentCode); }
    if (event.key === "Enter" || event.key === " ") onSelect(node.code);
  };
  const toggle = (code: string) => setExpanded((current) => { const next = new Set(current); if (next.has(code)) next.delete(code); else next.add(code); return next; });
  return <div className="tree-wrap"><button className={`tree-all ${selected === null ? "selected" : ""}`} onClick={() => onSelect(null)}><Wrench aria-hidden="true" />{messages.common.wholeVehicle}</button><div role="tree" aria-label={messages.vehicle.bom}>{visible.map((node, index) => <div role="treeitem" aria-level={(node.level ?? 0) + 1} aria-expanded={node.children.length ? expanded.has(node.code) : undefined} aria-selected={selected === node.code} key={node.code} className="tree-row" style={{ "--tree-indent": `${(node.level ?? 0) * 2}em` } as React.CSSProperties}><button type="button" className={`tree-label ${selected === node.code ? "selected" : ""}`} tabIndex={index === 0 ? 0 : -1} data-tree-code={node.code} onKeyDown={(event) => keyDown(event, node, index)} onClick={() => onSelect(node.code)}>{node.children.length ? <span className="tree-toggle" onClick={(event) => { event.stopPropagation(); toggle(node.code); }}>{expanded.has(node.code) ? <ChevronDown /> : <ChevronRight />}</span> : <span className="tree-dot" /> }<span><strong>{node.nameZh}</strong><small>{node.code}</small></span></button></div>)}</div></div>;
}
function flattenVisible(roots: BomTreeNode[], expanded: Set<string>): BomTreeNode[] { const result: BomTreeNode[] = []; const visit = (node: BomTreeNode) => { result.push(node); if (expanded.has(node.code)) node.children.forEach(visit); }; roots.forEach(visit); return result; }
