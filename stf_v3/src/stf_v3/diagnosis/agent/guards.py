"""Manual sub-agent guardrails (V2 hand-written loop logic, FM-40).

V2's manual sub-agent loop carried four guards that Pydantic AI does not
provide by itself; they live here as run state the manual tools consult:

1. **Manual pinning** — after the first ``list_manuals`` output, the one
   manual whose vehicle / factory code appears in the inquiry (or that
   matches the vehicle record) is pinned.
2. **Foreign-manual block** — TOC / section reads against any other
   manual return a corrective message instead of running; after
   ``MAX_FOREIGN_BLOCKS`` such attempts the run is forced to finish.
3. **Force-final backstop** — after ``MAX_SECTION_READS_BEFORE_FINAL``
   evidence reads (a search counts half), or a byte-identical repeated
   call, the tools are withheld and the model must answer from what it
   has.
4. **Slug canonicalisation** — done where sections are recorded
   (``manual_tools.resolve_section_slug``).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from stf_v3.diagnosis.agent.deps import ManualInfo

MAX_SECTION_READS_BEFORE_FINAL = 4
MAX_FOREIGN_BLOCKS = 2
_MATCH_SEPARATOR_RE = re.compile(r"[-_\s]+")
_MIN_MATCH_TOKEN_CHARS = 4

NUDGE_FINAL_INSTRUCTION = (
    "Your last message was not a final answer (no JSON).  If you still need "
    "information, continue with tool calls; otherwise return your final JSON "
    "answer now, in the exact format requested."
)

FORCE_FINAL_INSTRUCTION = (
    "You have now read several manual sections — enough to decide. "
    "Do NOT call any more tools.  Using ONLY the sections you have "
    "already read, return your final JSON answer now.  "
    "SINGLE-MANUAL RULE: use only sections from the manual matching "
    "the vehicle in the question — if any section you read came "
    "from a different vehicle's manual, discard it; another "
    "vehicle's specs or procedures are never evidence and must not "
    "appear in your answer or citations.  If the "
    "requested information is present, answer it with citations — "
    "and if it is a PROCEDURE, include every step, prerequisite, "
    "warning, torque/spec value, and post-completion step the read "
    "sections state (a numbered list is fine); do not drop steps to "
    "shorten the summary.  SUB-QUESTION COVERAGE: if the question "
    "has multiple parts, your answer must address EVERY part — "
    "answer each part the read sections support, and apply the "
    "honesty rule below per part for the rest; never let one "
    "answered part justify silently dropping another.  If it is "
    "genuinely absent — the "
    "question's premise is wrong (e.g. the vehicle has no such "
    "system or the DTC is not defined) — return the Not-found shape "
    '{"summary": "Not found: <short explanation>", "citations": []} '
    "and, where useful, state what the manual DOES cover instead of "
    "a bare refusal.  HONESTY RULE for absence claims: say 'the "
    "manual does not contain X' ONLY if a search_manual_text call "
    "for X returned 0 matches AND no unread TOC title "
    "plausibly covers X; otherwise say 'not found in the sections "
    "read (<section titles>)' and name the unread TOC title (or "
    "unread search hit) that may cover it."
)

FORCED_DECLINE_SUMMARY = (
    "Not found: the available service manuals do not contain "
    "information answering this question."
)


def _norm(text: str) -> str:
    return _MATCH_SEPARATOR_RE.sub("", (text or "").lower())


def pin_manual_for_inquiry(
    inquiry_text: str, manuals: List[ManualInfo],
) -> Optional[Tuple[str, str]]:
    """The single manual named by the inquiry (factory code or vehicle tokens).

    Returns ``(manual_id, match_source)`` when EXACTLY ONE manual matches;
    ``None`` when zero or several do (the model's judgment governs).
    """
    haystack = _norm(inquiry_text)
    matches: List[Tuple[str, str]] = []
    for m in manuals:
        source: Optional[str] = None
        code = _norm(m.factory_code or "")
        if len(code) >= _MIN_MATCH_TOKEN_CHARS and code in haystack:
            source = "factory_code"
        else:
            for candidate in [m.canonical] + m.canonical.split():
                token = _norm(candidate)
                if len(token) >= _MIN_MATCH_TOKEN_CHARS and token in haystack:
                    source = "vehicle"
                    break
        if source is not None:
            matches.append((m.id, source))
    return matches[0] if len(matches) == 1 else None


def blocked_message(requested_id: str, pinned: ManualInfo, manuals: List[ManualInfo]) -> str:
    """Corrective tool output for a read against a foreign manual."""
    requested_vehicle = next((m.canonical for m in manuals if m.id == requested_id), "a different vehicle")
    code_part = f', factory_code="{pinned.factory_code}"' if pinned.factory_code else ""
    return (
        f"BLOCKED: manual '{requested_id}' is for \"{requested_vehicle}\", but this inquiry's vehicle "
        f"matches manual '{pinned.id}' (vehicle=\"{pinned.canonical}\"{code_part}).  Another vehicle's "
        f"manual is never evidence for this inquiry.  Use manual '{pinned.id}'."
    )


@dataclass
class ManualGuardState:
    """Per-run guard state consulted by the manual tools."""

    inquiry_text: str
    manuals: List[ManualInfo]
    pinned: Optional[ManualInfo] = None
    pin_attempted: bool = False
    pin_source: Optional[str] = None
    foreign_block_counts: Dict[str, int] = field(default_factory=dict)
    foreign_spin: bool = False
    section_reads: int = 0
    search_calls: int = 0
    seen_signatures: Set[str] = field(default_factory=set)
    repeated_call: bool = False
    force_final: bool = False
    blocked_calls: int = 0

    def pin_from_vehicle(self, manufacturer: str, model: str) -> None:
        """Pin the manual matching the vehicle record when exactly one does."""
        from stf_v3.diagnosis.tools.manual_tools import manual_matches_vehicle

        hits = [m for m in self.manuals if manual_matches_vehicle(m, manufacturer, model)]
        if len(hits) == 1:
            self.pinned, self.pin_attempted, self.pin_source = hits[0], True, "vehicle_record"

    def on_list_manuals(self) -> None:
        """Pin once from the inquiry text (V2: from the first list_manuals output)."""
        if self.pin_attempted:
            return
        self.pin_attempted = True
        pin = pin_manual_for_inquiry(self.inquiry_text, self.manuals)
        if pin is not None:
            self.pinned = next(m for m in self.manuals if m.id == pin[0])
            self.pin_source = pin[1]

    def check_access(self, manual_id: str) -> Optional[str]:
        """Blocked message when ``manual_id`` is not the pinned manual."""
        if self.pinned is None or not manual_id or manual_id == self.pinned.id:
            return None
        count = self.foreign_block_counts.get(manual_id, 0) + 1
        self.foreign_block_counts[manual_id] = count
        self.blocked_calls += 1
        if count >= MAX_FOREIGN_BLOCKS:
            self.foreign_spin = True
            self.force_final = True
        return blocked_message(manual_id, self.pinned, self.manuals)

    def after_call(self, name: str, args: Dict[str, Any]) -> None:
        """Update evidence counters + repeat detection; may trip force_final."""
        signature = name + ":" + json.dumps(args, sort_keys=True, default=str)
        if signature in self.seen_signatures:
            self.repeated_call = True
        self.seen_signatures.add(signature)
        if name == "read_manual_section":
            self.section_reads += 1
        elif name == "search_manual_text":
            self.search_calls += 1
        if (self.section_reads + self.search_calls / 2 >= MAX_SECTION_READS_BEFORE_FINAL
                or self.repeated_call or self.foreign_spin):
            self.force_final = True
