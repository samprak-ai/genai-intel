"use client";
/**
 * DistributionChart — pie chart for cloud/AI provider distribution.
 * Shows percentage labels inside each slice, with a rich tooltip.
 */

import {
  PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import type { PieLabelRenderProps } from "recharts";
import { ProviderDistribution } from "@/lib/api";

const CLOUD_PALETTE: Record<string, string> = {
  AWS:           "#F97316",  // orange
  GCP:           "#22C55E",  // green
  Azure:         "#3B82F6",  // blue
  "Multi-Cloud": "#6366F1",  // indigo — mixed hyperscaler usage
  Hybrid:        "#14B8A6",  // teal   — cloud + on-premises mix
  "On-Premises": "#64748B",  // slate  — own infrastructure
  Other:         "#9CA3AF",  // gray   — neo/GPU clouds (CoreWeave, Lambda etc.)
  Unknown:       "#93C5FD",  // blue-300 — clearly distinct from gray Other
};

const AI_PALETTE: Record<string, string> = {
  Anthropic:        "#D97706",   // amber
  OpenAI:           "#10B981",   // emerald
  "Multi-Provider": "#6366F1",   // indigo — mixed AI usage
  Other:            "#9CA3AF",   // gray
  Unknown:          "#93C5FD",   // blue-300 — clearly distinct from gray Other
};

/** AI providers to keep as individual slices; everything else → "Other" */
const AI_KEEP = new Set(["OpenAI", "Anthropic", "Multi-Provider", "Unknown"]);

interface Props {
  data: ProviderDistribution[];
  type: "cloud" | "ai";
}

/** Group minor AI providers into "Other" */
function prepareAIData(raw: ProviderDistribution[]): ProviderDistribution[] {
  const kept: ProviderDistribution[] = [];
  let otherCount = 0;

  for (const entry of raw) {
    if (AI_KEEP.has(entry.provider)) {
      kept.push(entry);
    } else {
      otherCount += entry.startup_count;
    }
  }

  if (otherCount > 0) {
    kept.push({ provider: "Other", startup_count: otherCount, multi_cloud_count: 0, sole_provider_count: 0, avg_confidence: 0 });
  }

  // Sort descending by count, Unknown last
  return kept.sort((a, b) => {
    if (a.provider === "Unknown") return 1;
    if (b.provider === "Unknown") return -1;
    return b.startup_count - a.startup_count;
  });
}

/** Render "30%" labels inside each slice */
function renderLabel({
  cx, cy, midAngle, innerRadius, outerRadius, percent,
}: PieLabelRenderProps) {
  const RADIAN = Math.PI / 180;
  const cxNum = Number(cx ?? 0);
  const cyNum = Number(cy ?? 0);
  const inner = Number(innerRadius ?? 0);
  const outer = Number(outerRadius ?? 0);
  const pct   = Number(percent ?? 0);

  // Skip tiny slices
  if (pct < 0.06) return null;

  const angle  = -Number(midAngle ?? 0) * RADIAN;
  const radius = (inner + outer) / 2;
  const x      = cxNum + radius * Math.cos(angle);
  const y      = cyNum + radius * Math.sin(angle);

  return (
    <text
      x={x}
      y={y}
      textAnchor="middle"
      dominantBaseline="central"
      fontSize={11}
      fontWeight={600}
      fill="#fff"
    >
      {`${(pct * 100).toFixed(0)}%`}
    </text>
  );
}

export function DistributionChart({ data, type }: Props) {
  const palette   = type === "cloud" ? CLOUD_PALETTE : AI_PALETTE;
  const chartData = type === "ai" ? prepareAIData(data) : data;
  const total     = chartData.reduce((s, r) => s + r.startup_count, 0);

  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie
          data={chartData}
          dataKey="startup_count"
          nameKey="provider"
          cx="50%"
          cy="46%"
          outerRadius={90}
          paddingAngle={2}
          label={renderLabel}
          labelLine={false}
        >
          {chartData.map((entry) => (
            <Cell
              key={entry.provider}
              fill={palette[entry.provider] ?? "#9CA3AF"}
            />
          ))}
        </Pie>
        <Tooltip
          formatter={(v: number | string | undefined, name: string | undefined) => {
            const n   = Number(v ?? 0);
            const pct = total > 0 ? ((n / total) * 100).toFixed(1) : "0";
            return [`${n} startups (${pct}%)`, name ?? ""];
          }}
          contentStyle={{ fontSize: 12 }}
        />
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}
