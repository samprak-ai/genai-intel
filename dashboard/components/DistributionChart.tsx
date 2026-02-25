"use client";
/**
 * DistributionChart — donut pie chart for cloud/AI provider distribution.
 * Uses Recharts PieChart to show proportional share at a glance.
 */

import {
  PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { ProviderDistribution } from "@/lib/api";

const CLOUD_PALETTE: Record<string, string> = {
  AWS:       "#FF9900",
  GCP:       "#4285F4",
  Azure:     "#0089D6",
  CoreWeave: "#7C3AED",
};

const AI_PALETTE: Record<string, string> = {
  Anthropic:  "#D97706",
  OpenAI:     "#10B981",
  "Google AI": "#4285F4",
  Cohere:     "#7C3AED",
  Mistral:    "#F43F5E",
};

interface Props {
  data: ProviderDistribution[];
  type: "cloud" | "ai";
}

export function DistributionChart({ data, type }: Props) {
  const palette = type === "cloud" ? CLOUD_PALETTE : AI_PALETTE;

  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie
          data={data}
          dataKey="startup_count"
          nameKey="provider"
          cx="50%"
          cy="50%"
          innerRadius={55}
          outerRadius={85}
          paddingAngle={2}
        >
          {data.map((entry) => (
            <Cell
              key={entry.provider}
              fill={palette[entry.provider] ?? "#94A3B8"}
            />
          ))}
        </Pie>
        <Tooltip
          formatter={(v: number | string | undefined) => [`${v ?? 0} startups`, "Count"]}
          contentStyle={{ fontSize: 12 }}
        />
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}
