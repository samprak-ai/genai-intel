/**
 * ProviderBadge — coloured chip for cloud and AI provider names.
 * Handles single providers, multi-cloud strings, "Unknown", and "Not Applicable".
 */

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const CLOUD_COLORS: Record<string, string> = {
  AWS:       "bg-orange-100 text-orange-800 border-orange-200",
  GCP:       "bg-blue-100   text-blue-800   border-blue-200",
  Azure:     "bg-sky-100    text-sky-800    border-sky-200",
  CoreWeave: "bg-purple-100 text-purple-800 border-purple-200",
};

const AI_COLORS: Record<string, string> = {
  Anthropic:  "bg-amber-100  text-amber-800  border-amber-200",
  OpenAI:     "bg-emerald-100 text-emerald-800 border-emerald-200",
  "Google AI": "bg-blue-100  text-blue-800   border-blue-200",
  Cohere:     "bg-violet-100 text-violet-800 border-violet-200",
  Mistral:    "bg-rose-100   text-rose-800   border-rose-200",
};

interface Props {
  name?: string | null;
  isMulti?: boolean;
  providers?: string[];
  isNotApplicable?: boolean;
  type?: "cloud" | "ai";
  className?: string;
}

export function ProviderBadge({
  name,
  isMulti,
  providers = [],
  isNotApplicable,
  type = "cloud",
  className,
}: Props) {
  const colorMap = type === "cloud" ? CLOUD_COLORS : AI_COLORS;

  if (isNotApplicable) {
    return (
      <Badge variant="outline" className={cn("text-gray-500 border-gray-300", className)}>
        N/A
      </Badge>
    );
  }

  if (!name || name === "Unknown") {
    return (
      <Badge variant="outline" className={cn("text-gray-400 border-gray-200", className)}>
        Unknown
      </Badge>
    );
  }

  if (isMulti && providers.length > 0) {
    return (
      <div className={cn("flex flex-wrap gap-1", className)}>
        {providers.map((p) => (
          <Badge
            key={p}
            variant="outline"
            className={colorMap[p] ?? "bg-gray-100 text-gray-700 border-gray-200"}
          >
            {p}
          </Badge>
        ))}
      </div>
    );
  }

  const colorClass = colorMap[name] ?? "bg-gray-100 text-gray-700 border-gray-200";
  return (
    <Badge variant="outline" className={cn(colorClass, className)}>
      {name}
    </Badge>
  );
}
