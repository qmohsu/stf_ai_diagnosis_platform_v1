"use client";

import { Globe2 } from "lucide-react";
import { useRef } from "react";
import { useLocale } from "@/components/locale-provider";
import type { Locale } from "@/lib/domain";

const options: Locale[] = ["zh-HK", "zh-CN"];

export function LanguageSwitch() {
  const { locale, messages, setLocale, announcement } = useLocale();
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  const onKeyDown = (event: React.KeyboardEvent, index: number) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let next = index;
    if (event.key === "ArrowLeft") next = (index + options.length - 1) % options.length;
    if (event.key === "ArrowRight") next = (index + 1) % options.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = options.length - 1;
    refs.current[next]?.focus();
    void setLocale(options[next]);
  };

  return (
    <div className="language-wrap">
      <Globe2 aria-hidden="true" size={18} />
      <div className="language-switch" role="radiogroup" aria-label={messages.common.language}>
        {options.map((option, index) => (
          <button
            key={option}
            ref={(element) => { refs.current[index] = element; }}
            type="button"
            role="radio"
            aria-checked={locale === option}
            tabIndex={locale === option ? 0 : -1}
            className={locale === option ? "active" : ""}
            onClick={() => void setLocale(option)}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            {option === "zh-HK" ? messages.traditional : messages.simplified}
          </button>
        ))}
      </div>
      <span className="sr-only" aria-live="polite">{announcement}</span>
    </div>
  );
}
