/**
 * EntrenchmentChip — STRONG / MODERATE / WEAK / UNKNOWN badge.
 */

import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/Tooltip";
import { cn } from "@/lib/utils";

const COLORS: Record<string, string> = {
  STRONG:   "bg-emerald-100 text-emerald-800 border-emerald-200",
  MODERATE: "bg-amber-100   text-amber-800   border-amber-200",
  WEAK:     "bg-red-100     text-red-700     border-red-200",
  UNKNOWN:  "bg-gray-100    text-gray-500    border-gray-200",
};

interface Props {
  level?: string | null;
  className?: string;
}

export function EntrenchmentChip({ level, className }: Props) {
  if (!level) return null;
  return (
    <Tooltip text="How deeply integrated the provider is, based on signal strength and diversity">
      <Badge
        variant="outline"
        className={cn("text-xs font-medium", COLORS[level] ?? COLORS.UNKNOWN, className)}
      >
        {level}
      </Badge>
    </Tooltip>
  );
}
