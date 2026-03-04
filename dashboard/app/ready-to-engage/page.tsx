"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { getStartups, StartupRow } from "@/lib/api";
import { ProviderBadge } from "@/components/ProviderBadge";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { EngagementTimingChip } from "@/components/EngagementTimingChip";
import { EngagementTierChip } from "@/components/EngagementTierChip";
import { PropensityChip } from "@/components/PropensityChip";
import { TriggerBadge } from "@/components/TriggerBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";

const TIMING_ORDER: Record<string, number> = { Hot: 0, Warm: 1, Watch: 2 };

export default function ReadyToEngagePage() {
  const [rows, setRows] = useState<StartupRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [includeTier2, setIncludeTier2] = useState(false);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    (async () => {
      try {
        const tier1 = await getStartups({ engagement_tier: "1", per_page: 100 });
        if (includeTier2) {
          const tier2 = await getStartups({ engagement_tier: "2", per_page: 100 });
          setRows([...tier1, ...tier2]);
        } else {
          setRows(tier1);
        }
      } catch { setRows([]); }
      finally { setLoading(false); }
    })();
  }, [includeTier2]);

  // Sort: Hot first, then Warm, then Watch, then by funding date recency
  const sorted = [...rows].sort((a, b) => {
    const ta = TIMING_ORDER[a.engagement_timing ?? "Watch"] ?? 2;
    const tb = TIMING_ORDER[b.engagement_timing ?? "Watch"] ?? 2;
    if (ta !== tb) return ta - tb;
    // Secondary sort: funding recency (most recent first)
    const da = a.funding_announcement_date ?? "";
    const db = b.funding_announcement_date ?? "";
    return db.localeCompare(da);
  });

  function daysAgo(dateStr?: string): string {
    if (!dateStr) return "—";
    const d = new Date(dateStr);
    const now = new Date();
    const diff = Math.floor((now.getTime() - d.getTime()) / (1000 * 60 * 60 * 24));
    if (diff <= 0) return "today";
    if (diff === 1) return "1d ago";
    return `${diff}d ago`;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Ready to Engage</h1>
          <p className="text-sm text-gray-500 mt-1">
            {includeTier2 ? "Tier 1 + Tier 2" : "Tier 1"} companies with engagement intelligence
          </p>
        </div>
        <Button
          variant={includeTier2 ? "default" : "outline"}
          size="sm"
          onClick={() => setIncludeTier2((v) => !v)}
        >
          {includeTier2 ? "Showing Tier 1 + 2" : "Show Tier 2 too"}
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="p-4 space-y-3">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : sorted.length === 0 ? (
            <p className="text-sm text-gray-400 py-12 text-center">
              No {includeTier2 ? "Tier 1 or 2" : "Tier 1"} companies found.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Company</TableHead>
                  <TableHead>Vertical</TableHead>
                  <TableHead>Propensity</TableHead>
                  <TableHead>Timing</TableHead>
                  <TableHead>Tier</TableHead>
                  <TableHead>Triggers</TableHead>
                  <TableHead>Funding</TableHead>
                  <TableHead>Cloud</TableHead>
                  <TableHead>Conf</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((r) => (
                  <>
                    <TableRow
                      key={r.id}
                      className="cursor-pointer hover:bg-gray-50"
                      onClick={() => setExpandedRow(expandedRow === r.id ? null : r.id)}
                    >
                      <TableCell>
                        <Link href={`/companies/${r.id}`} className="font-medium text-blue-600 hover:underline" onClick={(e) => e.stopPropagation()}>
                          {r.canonical_name}
                        </Link>
                        <div className="text-xs text-gray-400">{r.website}</div>
                      </TableCell>
                      <TableCell className="text-sm">
                        <div>{r.vertical ?? "—"}</div>
                        {r.sub_vertical && <div className="text-xs text-gray-400">{r.sub_vertical}</div>}
                      </TableCell>
                      <TableCell><PropensityChip propensity={r.cloud_propensity} /></TableCell>
                      <TableCell><EngagementTimingChip timing={r.engagement_timing} /></TableCell>
                      <TableCell><EngagementTierChip tier={r.engagement_tier} /></TableCell>
                      <TableCell><TriggerBadge count={r.active_trigger_count} /></TableCell>
                      <TableCell className="text-sm">
                        {r.funding_amount_usd ? (
                          <div>
                            <span className="font-medium">${r.funding_amount_usd}M</span>
                            <div className="text-xs text-gray-400">{daysAgo(r.funding_announcement_date)}</div>
                          </div>
                        ) : "—"}
                      </TableCell>
                      <TableCell>
                        <ProviderBadge name={r.cloud_primary_provider} isMulti={r.cloud_is_multi} providers={r.cloud_providers} isNotApplicable={r.cloud_not_applicable} type="cloud" />
                      </TableCell>
                      <TableCell className="w-24"><ConfidenceBar value={r.cloud_confidence} /></TableCell>
                    </TableRow>
                    {expandedRow === r.id && (
                      <TableRow key={`${r.id}-detail`}>
                        <TableCell colSpan={9} className="bg-gray-50 px-6 py-4">
                          <div className="space-y-3">
                            {r.recommended_angle ? (
                              <div>
                                <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Recommended Angle</p>
                                <p className="text-sm leading-relaxed text-gray-700">{r.recommended_angle}</p>
                              </div>
                            ) : (
                              <p className="text-sm text-gray-400 italic">No engagement angle generated yet.</p>
                            )}
                            {(() => {
                              const raw = r.key_signals;
                              const signals: string[] = Array.isArray(raw) ? raw : typeof raw === "string" ? (() => { try { return JSON.parse(raw); } catch { return []; } })() : [];
                              return signals.length > 0 ? (
                                <div>
                                  <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Key Signals</p>
                                  <ul className="text-sm space-y-0.5">
                                    {signals.map((signal, i) => (
                                      <li key={i} className="text-gray-600">• {signal}</li>
                                    ))}
                                  </ul>
                                </div>
                              ) : null;
                            })()}
                            {r.intelligence_generated_at && (
                              <p className="text-xs text-gray-400">
                                Generated {new Date(r.intelligence_generated_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
                              </p>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
