"use client";

import { useState } from "react";
import { Star } from "lucide-react";
import { cn } from "@/lib/utils";

interface StarRatingProps {
  /** Current value (1-5) or null for unset. */
  value: number | null;
  /** Called when user clicks a star.  Pass null to clear. */
  onChange: (value: number | null) => void;
  /** Visible label above the stars. */
  label: string;
  /** Optional helper text below. */
  description?: string;
  /** Disable interaction (read-only). */
  disabled?: boolean;
  /** Star pixel size; default 28. */
  size?: number;
}

/**
 * 5-star input with hover preview + click-to-clear.
 *
 * Click a star to set the rating; click the same star again to
 * clear (set to null).  Hover preview shows what the rating
 * WOULD be without committing.  Designed for the golden-review
 * dashboard's per-dimension and overall ratings.
 */
export function StarRating({
  value,
  onChange,
  label,
  description,
  disabled = false,
  size = 28,
}: StarRatingProps) {
  const [hover, setHover] = useState<number | null>(null);
  const display = hover ?? value ?? 0;

  return (
    <div className="space-y-1">
      <div className="text-sm font-medium">{label}</div>
      <div className="flex items-center gap-1">
        {[1, 2, 3, 4, 5].map((n) => {
          const filled = n <= display;
          return (
            <button
              key={n}
              type="button"
              disabled={disabled}
              onMouseEnter={() => !disabled && setHover(n)}
              onMouseLeave={() => setHover(null)}
              onClick={() => {
                if (disabled) return;
                // Click same star to clear, otherwise set.
                onChange(value === n ? null : n);
              }}
              className={cn(
                "p-0.5 transition-transform",
                !disabled && "hover:scale-110 cursor-pointer",
                disabled && "cursor-not-allowed opacity-60",
              )}
              aria-label={`${label}: ${n} star${n > 1 ? "s" : ""}`}
            >
              <Star
                style={{ width: size, height: size }}
                className={cn(
                  "transition-colors",
                  filled
                    ? "fill-yellow-400 text-yellow-400"
                    : "fill-none text-muted-foreground",
                )}
              />
            </button>
          );
        })}
        <span className="ml-2 text-sm text-muted-foreground tabular-nums">
          {value !== null ? `${value}/5` : "—"}
        </span>
      </div>
      {description && (
        <div className="text-xs text-muted-foreground">
          {description}
        </div>
      )}
    </div>
  );
}
