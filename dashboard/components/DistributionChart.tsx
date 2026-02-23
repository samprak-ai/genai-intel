"use client";
/**
 * DistributionChart — horizontal bar chart for cloud/AI provider distribution.
 * Uses Recharts BarChart in horizontal layout.
 */

import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
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
      <BarChart
        layout="vertical"
        data={data}
        margin={{ top: 4, right: 24, left: 8, bottom: 4 }}
      >
        <XAxis type="number" tick={{ fontSize: 12 }} />
        <YAxis
          dataKey="provider"
          type="category"
          width={90}
          tick={{ fontSize: 12 }}
        />
        <Tooltip
          formatter={(v: number | string | undefined) => [`${v ?? 0} startups`, "Count"]}
          contentStyle={{ fontSize: 12 }}
        />
        <Bar dataKey="startup_count" radius={[0, 4, 4, 0]}>
          {data.map((entry) => (
            <Cell
              key={entry.provider}
              fill={palette[entry.provider] ?? "#94A3B8"}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
