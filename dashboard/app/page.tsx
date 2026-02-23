import { getSummary, getRecentFunding } from "@/lib/api";
import { DistributionChart } from "@/components/DistributionChart";
import { ProviderBadge } from "@/components/ProviderBadge";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const [summary, recentFunding] = await Promise.all([getSummary(), getRecentFunding(15)]);
  const topCloud = summary.cloud_distribution[0];
  const topAI    = summary.ai_distribution[0];
  const total    = summary.cloud_distribution.reduce((s, r) => s + r.startup_count, 0) || summary.total_companies;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-gray-500 mt-1">Cloud and AI provider attribution for AI startups</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Companies Tracked" value={String(total)} />
        <KpiCard label="Top Cloud" value={topCloud?.provider ?? "—"} sub={topCloud ? `${topCloud.startup_count} startups` : ""} />
        <KpiCard label="Top AI" value={topAI?.provider ?? "—"} sub={topAI ? `${topAI.startup_count} startups` : ""} />
        <KpiCard label="Last Run" value={summary.latest_run?.status ?? "Never"} sub={summary.latest_run?.run_date ?? ""} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Cloud Providers</CardTitle></CardHeader>
          <CardContent>
            {summary.cloud_distribution.length > 0
              ? <DistributionChart data={summary.cloud_distribution} type="cloud" />
              : <p className="text-sm text-gray-400 py-8 text-center">No data yet</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">AI Providers</CardTitle></CardHeader>
          <CardContent>
            {summary.ai_distribution.length > 0
              ? <DistributionChart data={summary.ai_distribution} type="ai" />
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
                      <TableCell><ProviderBadge name={r.cloud_display} type="cloud" /></TableCell>
                      <TableCell className="w-28"><ConfidenceBar value={r.cloud_confidence} /></TableCell>
                      <TableCell><ProviderBadge name={r.ai_display} type="ai" /></TableCell>
                      <TableCell className="text-gray-500 text-sm">{r.announcement_date}</TableCell>
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

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <CardContent className="pt-5 pb-4">
        <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">{label}</p>
        <p className="text-2xl font-bold">{value}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </CardContent>
    </Card>
  );
}
