"use client";
/**
 * VerticalChart — pie chart for vertical classification distribution.
 * Shows how tracked companies are distributed across industry verticals.
 */

import {
  PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import type { PieLabelRenderProps } from "recharts";
import { VerticalDistribution } from "@/lib/api";

const VERTICAL_PALETTE: Record<string, string> = {
  "AI Infrastructure & Compute":         "#7C3AED",  // violet
  "AI Applications & Tooling":           "#A855F7",  // purple
  "B2B SaaS / Enterprise":               "#3B82F6",  // blue
  "Climate & Energy Tech":               "#22C55E",  // green
  "Consumer / E-commerce & Marketplaces": "#F97316",  // orange
  "Cybersecurity":                        "#EF4444",  // red
  "Data Infrastructure":                  "#06B6D4",  // cyan
  "Developer Tools":                      "#6366F1",  // indigo
  "Education Tech":                       "#EC4899",  // pink
  "Fintech, Payments and Crypto":         "#F59E0B",  // amber
  "Healthcare, BioTech & Life Sciences":  "#10B981",  // emerald
  "HR Tech / Workforce Tech":             "#8B5CF6",  // violet-light
  "Industrial / IoT / Robotics":          "#64748B",  // slate
  "Legal Tech":                           "#78716C",  // stone
  "Aero / Defence / Space":               "#0EA5E9",  // sky
  "PropTech / Real Estate Tech":          "#D97706",  // amber-dark
  "Construction Tech / AEC":              "#92400E",  // amber-brown
};

interface Props {
  data: VerticalDistribution[];
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

export function VerticalChart({ data }: Props) {
  const total = data.reduce((s, r) => s + r.count, 0);

  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie
          data={data}
          dataKey="count"
          nameKey="vertical"
          cx="50%"
          cy="46%"
          outerRadius={90}
          paddingAngle={2}
          label={renderLabel}
          labelLine={false}
        >
          {data.map((entry) => (
            <Cell
              key={entry.vertical}
              fill={VERTICAL_PALETTE[entry.vertical] ?? "#9CA3AF"}
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
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}
