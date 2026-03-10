"use client";

import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid } from "recharts";
import type { SearchUsageDay } from "@/lib/api";

interface Props {
  data: SearchUsageDay[];
}

export function SearchUsageChart({ data }: Props) {
  // Format date labels to "Mar 5" style
  const formatted = data.map((d) => ({
    ...d,
    label: new Date(d.usage_date + "T00:00:00").toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    }),
  }));

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={formatted} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
        <Tooltip
          contentStyle={{ fontSize: 12 }}
          formatter={(value: unknown, name: unknown) => [
            String(value),
            name === "attribution" ? "Attribution" : name === "trigger_detection" ? "Triggers" : "Other",
          ]}
        />
        <Legend
          formatter={(value: string) =>
            value === "attribution" ? "Attribution" : value === "trigger_detection" ? "Triggers" : "Other"
          }
          iconSize={10}
          wrapperStyle={{ fontSize: 12 }}
        />
        <Bar dataKey="attribution" stackId="a" fill="#6366f1" radius={[0, 0, 0, 0]} />
        <Bar dataKey="trigger_detection" stackId="a" fill="#f59e0b" radius={[2, 2, 0, 0]} />
        {data.some((d) => d.other > 0) && (
          <Bar dataKey="other" stackId="a" fill="#94a3b8" radius={[2, 2, 0, 0]} />
        )}
      </BarChart>
    </ResponsiveContainer>
  );
}
