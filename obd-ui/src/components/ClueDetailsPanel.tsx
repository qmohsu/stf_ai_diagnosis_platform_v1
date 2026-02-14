"use client";

import { useMemo, useState } from "react";
import type { DiagnosticClue } from "@/lib/types";
import { ClueDetailCard } from "@/components/ClueDetailCard";
import { Select } from "@/components/ui/select";

interface ClueDetailsPanelProps {
  clueDetails: DiagnosticClue[];
}

export function ClueDetailsPanel({ clueDetails }: ClueDetailsPanelProps) {
  const [filterCategory, setFilterCategory] = useState<string>("all");

  const categories = useMemo(() => {
    const cats = new Set(clueDetails.map((c) => c.category));
    return Array.from(cats).sort();
  }, [clueDetails]);

  const filtered = useMemo(() => {
    if (filterCategory === "all") return clueDetails;
    return clueDetails.filter((c) => c.category === filterCategory);
  }, [clueDetails, filterCategory]);

  // Group by category for display
  const grouped = useMemo(() => {
    const groups: Record<string, DiagnosticClue[]> = {};
    filtered.forEach((clue) => {
      if (!groups[clue.category]) groups[clue.category] = [];
      groups[clue.category].push(clue);
    });
    return groups;
  }, [filtered]);

  if (clueDetails.length === 0) {
    return <p className="text-sm text-muted-foreground">No detailed clues available.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium">Category:</label>
        <Select
          value={filterCategory}
          onChange={(e) => setFilterCategory(e.target.value)}
          className="w-48"
        >
          <option value="all">All ({clueDetails.length})</option>
          {categories.map((cat) => (
            <option key={cat} value={cat}>
              {cat} ({clueDetails.filter((c) => c.category === cat).length})
            </option>
          ))}
        </Select>
      </div>

      {Object.entries(grouped).map(([category, clues]) => (
        <div key={category} className="space-y-2">
          <h4 className="text-sm font-semibold capitalize">{category}</h4>
          {clues.map((clue, i) => (
            <ClueDetailCard key={`${clue.rule_id}-${i}`} clue={clue} />
          ))}
        </div>
      ))}
    </div>
  );
}
