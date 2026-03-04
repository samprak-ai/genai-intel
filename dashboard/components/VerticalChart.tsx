"use client";
/**
 * VerticalChart — pie chart for vertical classification distribution.
 * Shows how tracked companies are distributed across industry verticals.
 * Groups smaller slices into "Other" to keep the chart readable.
 */

import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
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
  Other:                                  "#9CA3AF",  // gray
};

/** Short labels for the legend to avoid overflow */
const SHORT_LABELS: Record<string, string> = {
  "AI Infrastructure & Compute":         "AI Infra",
  "AI Applications & Tooling":           "AI Apps",
  "B2B SaaS / Enterprise":               "B2B SaaS",
  "Climate & Energy Tech":               "Climate",
  "Consumer / E-commerce & Marketplaces": "Consumer",
  "Cybersecurity":                        "Cybersecurity",
  "Data Infrastructure":                  "Data Infra",
  "Developer Tools":                      "Dev Tools",
  "Education Tech":                       "EdTech",
  "Fintech, Payments and Crypto":         "Fintech",
  "Healthcare, BioTech & Life Sciences":  "Healthcare",
  "HR Tech / Workforce Tech":             "HR Tech",
  "Industrial / IoT / Robotics":          "Industrial",
  "Legal Tech":                           "Legal Tech",
  "Aero / Defence / Space":               "Aero/Space",
  "PropTech / Real Estate Tech":          "PropTech",
  "Construction Tech / AEC":              "Construction",
};

const MAX_SLICES = 7;

interface ChartEntry {
  name: string;       // short label
  fullName: string;   // full vertical name (for tooltip)
  count: number;
}

interface Props {
  data: VerticalDistribution[];
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

/** Collapse smaller verticals into "Other" and shorten labels */
function prepareData(raw: VerticalDistribution[]): ChartEntry[] {
  // Data arrives sorted by count desc from API
  const top = raw.slice(0, MAX_SLICES);
  const rest = raw.slice(MAX_SLICES);

  const entries: ChartEntry[] = top.map((d) => ({
    name: SHORT_LABELS[d.vertical] ?? d.vertical,
    fullName: d.vertical,
    count: d.count,
  }));

  if (rest.length > 0) {
    const otherCount = rest.reduce((s, d) => s + d.count, 0);
    const otherNames = rest.map((d) => d.vertical).join(", ");
    entries.push({ name: "Other", fullName: otherNames, count: otherCount });
  }

  return entries;
}

export function VerticalChart({ data }: Props) {
  const chartData = prepareData(data);
  const total = chartData.reduce((s, r) => s + r.count, 0);

  return (
    <div>
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie
            data={chartData}
            dataKey="count"
            nameKey="name"
            cx="50%"
            cy="50%"
            outerRadius={80}
            paddingAngle={2}
            label={renderLabel}
            labelLine={false}
          >
            {chartData.map((entry) => (
              <Cell
                key={entry.name}
                fill={VERTICAL_PALETTE[entry.fullName] ?? VERTICAL_PALETTE[entry.name] ?? "#9CA3AF"}
              />
            ))}
          </Pie>
          <Tooltip
            formatter={(v: number | string | undefined, name: string | undefined) => {
              const n   = Number(v ?? 0);
              const pct = total > 0 ? ((n / total) * 100).toFixed(1) : "0";
              // Find full name for tooltip
              const entry = chartData.find((e) => e.name === name);
              const label = entry && entry.name !== entry.fullName ? entry.fullName : name;
              return [`${n} startups (${pct}%)`, label ?? ""];
            }}
            contentStyle={{ fontSize: 12 }}
          />
        </PieChart>
      </ResponsiveContainer>

      {/* Custom compact legend */}
      <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 px-2 -mt-2">
        {chartData.map((entry) => (
          <div key={entry.name} className="flex items-center gap-1" title={entry.fullName}>
            <span
              className="inline-block w-2 h-2 rounded-full flex-shrink-0"
              style={{ backgroundColor: VERTICAL_PALETTE[entry.fullName] ?? VERTICAL_PALETTE[entry.name] ?? "#9CA3AF" }}
            />
            <span className="text-[11px] text-gray-600">{entry.name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
