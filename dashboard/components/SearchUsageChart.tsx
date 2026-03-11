"use client";

import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid } from "recharts";
import type { SearchUsageDay } from "@/lib/api";

// Readable labels for source keys
const SOURCE_LABELS: Record<string, string> = {
  attr_partnership: "Partnership Search",
  attr_inverted: "Inverted Site Search",
  attr_broad_scan: "Broad Article Scan",
  attr_investor_boards: "Investor Board Search",
  attr_blog: "Blog Discovery",
  trigger_leadership: "Trigger: Leadership",
  trigger_product: "Trigger: Product Launch",
  trigger_partnership: "Trigger: Partnership",
  trigger_press: "Trigger: Press",
  // Legacy labels (from before granular tracking)
  attribution: "Attribution",
  trigger_detection: "Trigger Detection",
};

// Color palette — enough for all sources
const COLORS = [
  "#6366f1", // indigo
  "#f59e0b", // amber
  "#10b981", // emerald
  "#ef4444", // red
  "#8b5cf6", // violet
  "#06b6d4", // cyan
  "#f97316", // orange
  "#ec4899", // pink
  "#14b8a6", // teal
  "#94a3b8", // slate
];

function labelFor(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

interface Props {
  data: SearchUsageDay[];
  sources: string[];
}

export function SearchUsageChart({ data, sources }: Props) {
  const formatted = data.map((d) => ({
    ...d,
    label: new Date(d.usage_date + "T00:00:00").toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    }),
  }));

  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={formatted} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
        <Tooltip
          contentStyle={{ fontSize: 12 }}
          formatter={(value: unknown, name: unknown) => [String(value), labelFor(String(name))]}
        />
        <Legend
          formatter={(value: string) => labelFor(value)}
          iconSize={10}
          wrapperStyle={{ fontSize: 11 }}
        />
        {sources.map((src, i) => (
          <Bar
            key={src}
            dataKey={src}
            stackId="a"
            fill={COLORS[i % COLORS.length]}
            radius={i === sources.length - 1 ? [2, 2, 0, 0] : [0, 0, 0, 0]}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
