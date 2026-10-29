import type { BomNode, IdentityType } from "@/lib/domain";

const VIN_PATTERN = /^[A-HJ-NPR-Z0-9]{17}$/;

export function normalizePlate(value: string): string {
  return value.trim().toUpperCase().replace(/[\s-]+/g, "");
}

export function isValidPlate(value: string): boolean {
  return /^[A-Z]{1,3}\d{2,4}$/.test(normalizePlate(value));
}

export function normalizeVin(value: string): string {
  return value.trim().toUpperCase();
}

export function isValidVin(value: unknown): value is string {
  return typeof value === "string" && VIN_PATTERN.test(normalizeVin(value));
}

export function vehiclePath(identityType: IdentityType, identity: string): string {
  const normalized = identityType === "vin" ? normalizeVin(identity) : normalizePlate(identity);
  return `/vehicles/${identityType}/${encodeURIComponent(normalized)}`;
}

export interface BomTreeNode extends BomNode {
  children: BomTreeNode[];
  orphaned?: boolean;
}

export function buildBomTree(nodes: BomNode[]): BomTreeNode[] {
  const byCode = new Map<string, BomTreeNode>();
  for (const node of nodes) {
    if (!node.code || byCode.has(node.code)) continue;
    byCode.set(node.code, { ...node, children: [] });
  }

  const roots: BomTreeNode[] = [];
  for (const node of byCode.values()) {
    if (!node.parentCode) {
      roots.push(node);
      continue;
    }
    const parent = byCode.get(node.parentCode);
    if (!parent || createsCycle(node.code, parent.code, byCode)) {
      node.orphaned = true;
      roots.push(node);
      continue;
    }
    parent.children.push(node);
  }

  const sort = (items: BomTreeNode[]) => {
    items.sort((a, b) => a.code.localeCompare(b.code));
    items.forEach((item) => sort(item.children));
  };
  sort(roots);
  const assignDepth = (items: BomTreeNode[], depth: number) => {
    for (const item of items) {
      item.level = depth;
      assignDepth(item.children, depth + 1);
    }
  };
  assignDepth(roots, 0);
  return roots;
}

function createsCycle(childCode: string, parentCode: string, byCode: Map<string, BomTreeNode>): boolean {
  const seen = new Set([childCode]);
  let cursor: string | null = parentCode;
  while (cursor) {
    if (seen.has(cursor)) return true;
    seen.add(cursor);
    cursor = byCode.get(cursor)?.parentCode ?? null;
  }
  return false;
}
