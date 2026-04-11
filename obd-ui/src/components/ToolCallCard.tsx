"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { ToolInvocation } from "@/lib/types";

const OUTPUT_TRUNCATE_LEN = 500;

interface ToolCallCardProps {
  invocation: ToolInvocation;
  defaultExpanded?: boolean;
}

export function ToolCallCard({ invocation, defaultExpanded = false }: ToolCallCardProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(defaultExpanded);

  // Collapse when parent signals streaming ended (prop goes false).
  // Only update when the prop actively transitions — skip the
  // initial mount where useState already set the correct value.
  const prevExpandedRef = useRef(defaultExpanded);
  useEffect(() => {
    if (prevExpandedRef.current !== defaultExpanded) {
      prevExpandedRef.current = defaultExpanded;
      if (!defaultExpanded) setExpanded(false);
    }
  }, [defaultExpanded]);
  const [showFullOutput, setShowFullOutput] = useState(false);

  const { name, input, result, status } = invocation;

  const borderColor =
    status === "calling"
      ? "border-l-blue-500"
      : status === "error"
        ? "border-l-red-500"
        : "border-l-green-500";

  const statusIcon =
    status === "calling" ? (
      <svg className="h-4 w-4 animate-spin text-blue-500" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
    ) : status === "error" ? (
      <span className="text-red-500 text-sm font-bold">&times;</span>
    ) : (
      <span className="text-green-500 text-sm font-bold">&check;</span>
    );

  const rawOutput = result?.output ?? "";
  const outputText = rawOutput || (result && !result.is_error
    ? `(${name} completed with no output)`
    : rawOutput);
  const needsTruncation = outputText.length > OUTPUT_TRUNCATE_LEN;
  const displayOutput = showFullOutput ? outputText : outputText.slice(0, OUTPUT_TRUNCATE_LEN);

  // Compact input summary for the header
  const inputKeys = Object.keys(input);
  const inputSummary = inputKeys.length > 0
    ? inputKeys.map((k) => `${k}: ${JSON.stringify(input[k])}`).join(", ")
    : "";
  const truncatedInput = inputSummary.length > 60
    ? inputSummary.slice(0, 57) + "..."
    : inputSummary;

  return (
    <div className={`border-l-4 ${borderColor} border rounded-md bg-card`}>
      {/* Header — always visible */}
      <button
        type="button"
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-muted/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
        aria-label={expanded ? t("agent.collapseTool") : t("agent.expandTool")}
      >
        {statusIcon}
        <Badge variant="secondary" className="font-mono text-xs">
          {name}
        </Badge>
        {truncatedInput && (
          <span className="text-xs text-muted-foreground truncate flex-1">
            {truncatedInput}
          </span>
        )}
        {result && (
          <span className="text-xs text-muted-foreground whitespace-nowrap ml-auto">
            {t("agent.toolDuration", { ms: Math.round(result.duration_ms) })}
          </span>
        )}
        <svg
          className={`h-4 w-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
          viewBox="0 0 20 20"
          fill="currentColor"
        >
          <path
            fillRule="evenodd"
            d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
            clipRule="evenodd"
          />
        </svg>
      </button>

      {/* Body — collapsible */}
      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t">
          {/* Input */}
          <div>
            <span className="text-xs font-medium text-muted-foreground">
              {t("agent.toolInput")}
            </span>
            <pre className="text-xs bg-muted/50 rounded p-2 mt-1 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(input, null, 2)}
            </pre>
          </div>

          {/* Output */}
          {result && (
            <div>
              <span className={`text-xs font-medium ${result.is_error ? "text-red-500" : "text-muted-foreground"}`}>
                {t("agent.toolOutput")}
              </span>
              <pre className={`text-xs rounded p-2 mt-1 overflow-x-auto whitespace-pre-wrap ${result.is_error ? "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200" : "bg-muted/50"}`}>
                {displayOutput}
                {needsTruncation && !showFullOutput && "..."}
              </pre>
              {needsTruncation && (
                <button
                  type="button"
                  className="text-xs text-primary hover:underline mt-1"
                  onClick={(e) => { e.stopPropagation(); setShowFullOutput(!showFullOutput); }}
                >
                  {showFullOutput ? t("agent.showLess") : t("agent.showMore")}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
