/**
 * ConfidenceBar — horizontal progress bar showing attribution confidence %.
 * Green ≥ 80%, Yellow ≥ 50%, Red < 50%.
 */

import { cn } from "@/lib/utils";

interface Props {
  value?: number | null; // 0.0–1.0
  isNotApplicable?: boolean;
  className?: string;
}

export function ConfidenceBar({ value, isNotApplicable, className }: Props) {
  if (isNotApplicable) {
    return <span className="text-xs text-gray-400">N/A</span>;
  }

  if (value == null) {
    return <span className="text-xs text-gray-400">—</span>;
  }

  const pct = Math.round(value * 100);
  const color =
    pct >= 80 ? "bg-emerald-500" :
    pct >= 50 ? "bg-amber-400"   :
                "bg-red-400";

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-gray-600 w-8 text-right">{pct}%</span>
    </div>
  );
}
