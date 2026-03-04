/**
 * EngagementTimingChip — Hot / Warm / Watch badge.
 * Hot = red (Tier 1 + recent strong triggers), Warm = amber (Tier 1/2), Watch = blue (Tier 3).
 */

import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/Tooltip";
import { cn } from "@/lib/utils";

const COLORS: Record<string, string> = {
  Hot:   "bg-red-100   text-red-700   border-red-200",
  Warm:  "bg-amber-100 text-amber-700 border-amber-200",
  Watch: "bg-blue-100  text-blue-700  border-blue-200",
};

const TOOLTIPS: Record<string, string> = {
  Hot:   "Tier 1 company with recent strong trigger — active decision window",
  Warm:  "Tier 1 or 2 company — monitor and engage on opportunity",
  Watch: "Tier 3 company — maintain and surface on change",
};

interface Props {
  timing?: string | null;
  className?: string;
}

export function EngagementTimingChip({ timing, className }: Props) {
  if (!timing) return <span className="text-gray-400 text-xs">—</span>;
  return (
    <Tooltip text={TOOLTIPS[timing] ?? timing}>
      <Badge
        variant="outline"
        className={cn("text-xs font-medium", COLORS[timing] ?? "bg-gray-100 text-gray-500 border-gray-200", className)}
      >
        {timing}
      </Badge>
    </Tooltip>
  );
}
