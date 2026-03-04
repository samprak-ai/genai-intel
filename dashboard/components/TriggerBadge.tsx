/**
 * TriggerBadge — shows active trigger count as a badge.
 * >=2 triggers = red, 1 trigger = amber, 0 = gray dash.
 */

import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/Tooltip";
import { cn } from "@/lib/utils";

interface Props {
  count?: number | null;
  className?: string;
}

export function TriggerBadge({ count, className }: Props) {
  if (!count || count === 0) return <span className="text-gray-400 text-xs">—</span>;

  const color = count >= 2
    ? "bg-red-100 text-red-800 border-red-200"
    : "bg-amber-100 text-amber-800 border-amber-200";

  return (
    <Tooltip text={`${count} active trigger${count !== 1 ? "s" : ""} detected`}>
      <Badge
        variant="outline"
        className={cn("text-xs font-medium", color, className)}
      >
        {count}
      </Badge>
    </Tooltip>
  );
}
