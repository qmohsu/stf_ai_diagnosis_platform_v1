import Image from "next/image";

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "brand-compact" : ""}`}>
      <Image src="/brand/towngas-mark.svg" width={compact ? 29 : 38} height={compact ? 36 : 47} style={{ width: compact ? 29 : 38, height: compact ? 36 : 47 }} alt="" priority />
      <div>
        <span className="brand-word">TOWNGAS</span>
        {!compact && <span className="brand-sub">The Hong Kong and China Gas Company Limited</span>}
      </div>
    </div>
  );
}
