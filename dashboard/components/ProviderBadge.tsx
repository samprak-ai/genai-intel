/**
 * ProviderBadge — coloured chip for cloud and AI provider names.
 * Handles single providers, multi-cloud strings, "Unknown", and "Not Applicable".
 *
 * Cloud display rules:
 *  - Multi-cloud (isMulti=true)   → single "Multi-Cloud (AWS, GCP)" badge
 *  - Non-major single cloud       → "Other (CoreWeave)" badge  (major = AWS, GCP, Azure)
 *  - Major single cloud           → coloured badge as-is
 */

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Providers shown with their own name (no "Other (...)" wrapper).
 * Hyperscalers + special infrastructure categories.
 */
const MAJOR_CLOUD_PROVIDERS = new Set([
  "AWS", "GCP", "Azure",
  // Infrastructure categories — display as-is, not as "Other (On-Premises)"
  "On-Premises", "Hybrid",
]);

const CLOUD_COLORS: Record<string, string> = {
  // Hyperscalers
  AWS:            "bg-orange-100  text-orange-800  border-orange-200",
  GCP:            "bg-blue-100    text-blue-800    border-blue-200",
  Azure:          "bg-sky-100     text-sky-800     border-sky-200",
  // Infrastructure categories
  "On-Premises":  "bg-slate-100   text-slate-800   border-slate-200",
  Hybrid:         "bg-teal-100    text-teal-800    border-teal-200",
  // Neo/GPU clouds (shown as "Other (X)" — colors used for that badge)
  CoreWeave:      "bg-purple-100  text-purple-800  border-purple-200",
  Lambda:         "bg-fuchsia-100 text-fuchsia-800 border-fuchsia-200",
  Crusoe:         "bg-lime-100    text-lime-800    border-lime-200",
  OVH:            "bg-indigo-100  text-indigo-800  border-indigo-200",
  Vultr:          "bg-cyan-100    text-cyan-800    border-cyan-200",
  Paperspace:     "bg-violet-100  text-violet-800  border-violet-200",
  Nebius:         "bg-rose-100    text-rose-800    border-rose-200",
  Fluidstack:     "bg-amber-100   text-amber-800   border-amber-200",
  "Vast.ai":      "bg-pink-100    text-pink-800    border-pink-200",
  OCI:            "bg-red-100     text-red-800     border-red-200",
  // Other hosting platforms
  Vercel:         "bg-gray-800    text-white       border-gray-700",
  Netlify:        "bg-teal-100    text-teal-800    border-teal-200",
  Cloudflare:     "bg-orange-100  text-orange-700  border-orange-300",
  Render:         "bg-indigo-100  text-indigo-800  border-indigo-200",
  Fastly:         "bg-red-100     text-red-800     border-red-200",
  "GitHub Pages": "bg-gray-100    text-gray-800    border-gray-300",
  Webflow:        "bg-blue-100    text-blue-700    border-blue-300",
};

const AI_COLORS: Record<string, string> = {
  // Established AI providers
  Anthropic:       "bg-amber-100   text-amber-800   border-amber-200",
  OpenAI:          "bg-emerald-100 text-emerald-800 border-emerald-200",
  "Google AI":     "bg-blue-100    text-blue-800    border-blue-200",
  Cohere:          "bg-violet-100  text-violet-800  border-violet-200",
  Mistral:         "bg-rose-100    text-rose-800    border-rose-200",
  // Open-source / inference providers
  "Meta / Llama":  "bg-sky-100     text-sky-800     border-sky-200",
  "xAI / Grok":    "bg-gray-800    text-white       border-gray-700",
  "Hugging Face":  "bg-yellow-100  text-yellow-800  border-yellow-200",
  "Together AI":   "bg-teal-100    text-teal-800    border-teal-200",
  Groq:            "bg-orange-100  text-orange-800  border-orange-200",
  Replicate:       "bg-purple-100  text-purple-800  border-purple-200",
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

  // Multi-provider: single badge with full provider list, rounded-md so it
  // wraps cleanly as a rectangular chip rather than an oval blob.
  //   cloud + On-Premises among providers → "Hybrid (AWS, On-Premises)"
  //   cloud multi                         → "Multi-Cloud (AWS, GCP)"
  //   ai multi                            → "Multi-Provider (Anthropic, OpenAI)"
  if (isMulti && providers.length > 0) {
    const providerList = providers.join(", ");
    const isHybrid = type === "cloud" && providers.includes("On-Premises");
    const label = isHybrid
      ? `Hybrid (${providerList})`
      : type === "cloud"
        ? `Multi-Cloud (${providerList})`
        : `Multi-Provider (${providerList})`;
    const badgeClass = isHybrid
      ? "bg-teal-100 text-teal-800 border-teal-200"
      : "bg-gray-100 text-gray-700 border-gray-300";
    return (
      <Badge variant="outline" className={cn(badgeClass, "!rounded-md whitespace-normal h-auto leading-5", className)}>
        {label}
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

  // For cloud type: non-major providers are labelled "Other (name)"
  if (type === "cloud" && !MAJOR_CLOUD_PROVIDERS.has(name)) {
    const colorClass = colorMap[name] ?? "bg-gray-100 text-gray-700 border-gray-200";
    return (
      <Badge variant="outline" className={cn(colorClass, className)}>
        Other ({name})
      </Badge>
    );
  }

  const colorClass = colorMap[name] ?? "bg-gray-100 text-gray-700 border-gray-200";
  return (
    <Badge variant="outline" className={cn(colorClass, className)}>
      {name}
    </Badge>
  );
}
