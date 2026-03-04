/**
 * EngagementTierChip — Tier 1 (Engage Now) / Tier 2 (Watch) / Tier 3 (Track) badge.
 * Red = Engage Now, Amber = Watch, Gray = Track.
 * Tooltip shows the tier rationale.
 */

import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/Tooltip";
import { cn } from "@/lib/utils";

const COLORS: Record<number, string> = {
  1: "bg-red-100 text-red-800 border-red-200",
  2: "bg-amber-100 text-amber-800 border-amber-200",
  3: "bg-gray-100 text-gray-500 border-gray-200",
};

const LABELS: Record<number, string> = {
  1: "Engage Now",
  2: "Watch",
  3: "Track",
};

interface Props {
  tier?: number | null;
  rationale?: string | null;
  className?: string;
}

export function EngagementTierChip({ tier, rationale, className }: Props) {
  if (!tier) return <span className="text-gray-400 text-xs">—</span>;
  return (
    <Tooltip text={rationale || LABELS[tier] || "Unknown"}>
      <Badge
        variant="outline"
        className={cn("text-xs font-medium", COLORS[tier] ?? COLORS[3], className)}
      >
        {LABELS[tier] ?? `Tier ${tier}`}
      </Badge>
    </Tooltip>
  );
}
