"use client";
/**
 * DistributionChart — donut pie chart for cloud/AI provider distribution.
 * Shows count + percentage labels on each slice, with a rich tooltip.
 */

import {
  PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import type { PieLabelRenderProps } from "recharts";
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

/** Render "25 (58%)" labels outside each slice */
function renderLabel({
  cx, cy, midAngle, outerRadius, percent, value,
}: PieLabelRenderProps) {
  const RADIAN = Math.PI / 180;
  const cxNum = Number(cx ?? 0);
  const cyNum = Number(cy ?? 0);
  const rNum  = Number(outerRadius ?? 0);
  const pct   = Number(percent ?? 0);

  // Skip tiny slices to avoid label overlap
  if (pct < 0.05) return null;

  const angle  = -Number(midAngle ?? 0) * RADIAN;
  const radius = rNum + 22;
  const x      = cxNum + radius * Math.cos(angle);
  const y      = cyNum + radius * Math.sin(angle);

  return (
    <text
      x={x}
      y={y}
      textAnchor="middle"
      dominantBaseline="central"
      fontSize={11}
      fill="#374151"
    >
      {`${value} (${(pct * 100).toFixed(0)}%)`}
    </text>
  );
}

export function DistributionChart({ data, type }: Props) {
  const palette = type === "cloud" ? CLOUD_PALETTE : AI_PALETTE;
  const total   = data.reduce((s, r) => s + r.startup_count, 0);

  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie
          data={data}
          dataKey="startup_count"
          nameKey="provider"
          cx="50%"
          cy="46%"
          innerRadius={52}
          outerRadius={78}
          paddingAngle={2}
          label={renderLabel}
          labelLine={false}
        >
          {data.map((entry) => (
            <Cell
              key={entry.provider}
              fill={palette[entry.provider] ?? "#94A3B8"}
            />
          ))}
        </Pie>
        <Tooltip
          formatter={(v: number | string | undefined) => {
            const n   = Number(v ?? 0);
            const pct = total > 0 ? ((n / total) * 100).toFixed(1) : "0";
            return [`${n} startups (${pct}%)`, "Count"];
          }}
          contentStyle={{ fontSize: 12 }}
        />
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}
