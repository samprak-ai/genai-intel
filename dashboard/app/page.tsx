import { getSummary, getRecentFunding, Summary } from "@/lib/api";
import { DistributionChart } from "@/components/DistributionChart";
import { VerticalChart } from "@/components/VerticalChart";
import { ProviderBadge } from "@/components/ProviderBadge";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  let summary: Summary = { total_companies: 0, cloud_distribution: [], ai_distribution: [] };
  let recentFunding: any[] = [];

  try {
    [summary, recentFunding] = await Promise.all([getSummary(), getRecentFunding(15)]);
  } catch (_e) {
    // API unreachable — render with empty state rather than crashing
  }

  const cloudDist    = summary.cloud_distribution ?? [];
  const aiDist       = summary.ai_distribution ?? [];
  const verticalDist = summary.vertical_distribution ?? [];
  const topCloud     = cloudDist.find(r => r.provider !== "Unknown");
  const topAI        = aiDist.find(r => r.provider !== "Unknown");
  const topVertical  = verticalDist[0];
  const total        = summary.total_companies || cloudDist.reduce((s, r) => s + r.startup_count, 0);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-gray-500 mt-1">Cloud and AI provider attribution for AI startups</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <KpiCard label="Companies Tracked" value={String(total)} />
        <KpiCard label="Top Cloud" value={topCloud?.provider ?? "—"} sub={topCloud ? `${topCloud.startup_count} startups` : ""} />
        <KpiCard label="Top AI" value={topAI?.provider ?? "—"} sub={topAI ? `${topAI.startup_count} startups` : ""} />
        <KpiCard label="Top Vertical" value={topVertical?.vertical ?? "—"} sub={topVertical ? `${topVertical.count} startups` : ""} small />
        <KpiCard label="Last Run" value={summary.latest_run?.status ?? "Never"} sub={summary.latest_run?.run_date ?? ""} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Cloud Providers</CardTitle></CardHeader>
          <CardContent>
            {cloudDist.length > 0
              ? <DistributionChart data={cloudDist} type="cloud" />
              : <p className="text-sm text-gray-400 py-8 text-center">No data yet</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">AI Providers</CardTitle></CardHeader>
          <CardContent>
            {aiDist.length > 0
              ? <DistributionChart data={aiDist} type="ai" />
              : <p className="text-sm text-gray-400 py-8 text-center">No data yet</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Verticals</CardTitle></CardHeader>
          <CardContent>
            {verticalDist.length > 0
              ? <VerticalChart data={verticalDist} />
              : <p className="text-sm text-gray-400 py-8 text-center">No data yet</p>}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">Recent Funding Events</CardTitle></CardHeader>
        <CardContent className="p-0">
          {recentFunding.length === 0
            ? <p className="text-sm text-gray-400 py-8 text-center">No data yet</p>
            : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Company</TableHead>
                    <TableHead>Round</TableHead>
                    <TableHead>Amount</TableHead>
                    <TableHead>Cloud</TableHead>
                    <TableHead>Cloud Conf</TableHead>
                    <TableHead>AI</TableHead>
                    <TableHead>Date</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(recentFunding as any[]).map((r, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-medium">{r.canonical_name}</TableCell>
                      <TableCell>{r.funding_round}</TableCell>
                      <TableCell>${r.funding_amount_usd}M</TableCell>
                      <TableCell><ProviderBadge name={r.cloud_primary_provider} isMulti={r.cloud_is_multi} providers={r.cloud_providers} isNotApplicable={r.cloud_not_applicable} type="cloud" /></TableCell>
                      <TableCell className="w-28"><ConfidenceBar value={r.cloud_confidence} /></TableCell>
                      <TableCell><ProviderBadge name={r.ai_primary_provider} isMulti={r.ai_is_multi} providers={r.ai_providers} isNotApplicable={r.ai_not_applicable} type="ai" /></TableCell>
                      <TableCell className="text-gray-500 text-sm">{r.announcement_date ? new Date(r.announcement_date).toLocaleDateString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' }) : '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
        </CardContent>
      </Card>
    </div>
  );
}

function KpiCard({ label, value, sub, small }: { label: string; value: string; sub?: string; small?: boolean }) {
  return (
    <Card>
      <CardContent className="pt-5 pb-4">
        <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">{label}</p>
        <p className={`${small ? "text-sm leading-snug" : "text-2xl"} font-bold ${small ? "" : "truncate"}`} title={value}>{value}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </CardContent>
    </Card>
  );
}
