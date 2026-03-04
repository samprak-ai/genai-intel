/**
 * PropensityChip — High / Medium / Low cloud propensity badge.
 * Green = High, Amber = Medium, Orange = Low.
 */

import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/Tooltip";
import { cn } from "@/lib/utils";

const COLORS: Record<string, string> = {
  High:   "bg-emerald-100 text-emerald-800 border-emerald-200",
  Medium: "bg-amber-100   text-amber-800   border-amber-200",
  Low:    "bg-orange-100   text-orange-700   border-orange-200",
};

interface Props {
  propensity?: string | null;
  className?: string;
}

export function PropensityChip({ propensity, className }: Props) {
  if (!propensity) return <span className="text-gray-400 text-xs">—</span>;
  return (
    <Tooltip text="Structural likelihood of becoming a significant cloud customer, based on sub-vertical classification">
      <Badge
        variant="outline"
        className={cn("text-xs font-medium", COLORS[propensity] ?? "bg-gray-100 text-gray-500 border-gray-200", className)}
      >
        {propensity}
      </Badge>
    </Tooltip>
  );
}
