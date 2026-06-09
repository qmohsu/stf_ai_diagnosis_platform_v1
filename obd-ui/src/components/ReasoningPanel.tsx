"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface ReasoningPanelProps {
  /** Accumulated reasoning text for the current thinking phase. */
  text: string;
  /** True while the agent is actively streaming (shows spinner). */
  active: boolean;
}

/**
 * Live, collapsible "Thinking…" panel that streams the agent's
 * chain-of-thought during each ReAct iteration.  Auto-scrolls to the
 * newest tokens while active, and can be collapsed by the user.  The
 * content is ephemeral — the parent clears it at each tool-call /
 * done boundary so the panel reflects only the current iteration.
 */
export function ReasoningPanel({ text, active }: ReasoningPanelProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the newest tokens while expanded.
  useEffect(() => {
    if (!collapsed && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [text, collapsed]);

  if (!text) return null;

  return (
    <div className="border rounded-md bg-muted/30">
      <button
        type="button"
        className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
        onClick={() => setCollapsed(!collapsed)}
        aria-label={collapsed ? t("agent.expandTool") : t("agent.collapseTool")}
      >
        {active ? (
          <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        ) : (
          <span aria-hidden>{"💭"}</span>
        )}
        <span className="font-medium">{t("agent.thinking")}</span>
        <svg
          className={`h-4 w-4 ml-auto transition-transform ${collapsed ? "" : "rotate-180"}`}
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

      {!collapsed && (
        <div ref={scrollRef} className="px-3 pb-3 max-h-48 overflow-y-auto border-t">
          <pre className="text-xs whitespace-pre-wrap font-sans text-muted-foreground leading-relaxed mt-2">
            {text}
          </pre>
        </div>
      )}
    </div>
  );
}
