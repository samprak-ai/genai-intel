"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { getStartups, StartupRow } from "@/lib/api";
import { ProviderBadge } from "@/components/ProviderBadge";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { EntrenchmentChip } from "@/components/EntrenchmentChip";
import { PropensityChip } from "@/components/PropensityChip";
import { EngagementTierChip } from "@/components/EngagementTierChip";
import { Tooltip } from "@/components/Tooltip";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";

const CLOUD_OPTIONS = [{ value: "all", label: "All clouds" }, { value: "AWS", label: "AWS" }, { value: "GCP", label: "GCP" }, { value: "Azure", label: "Azure" }, { value: "CoreWeave", label: "CoreWeave" }];
const AI_OPTIONS    = [{ value: "all", label: "All AI" }, { value: "Anthropic", label: "Anthropic" }, { value: "OpenAI", label: "OpenAI" }, { value: "Google AI", label: "Google AI" }, { value: "Cohere", label: "Cohere" }, { value: "Mistral", label: "Mistral" }];
const PROPENSITY_OPTIONS = [{ value: "all", label: "All propensity" }, { value: "High", label: "High" }, { value: "Medium", label: "Medium" }, { value: "Low", label: "Low" }];
const TIER_OPTIONS = [{ value: "all", label: "All tiers" }, { value: "1", label: "Engage Now" }, { value: "2", label: "Watch" }, { value: "3", label: "Track" }];
const VERTICAL_OPTIONS = [
  { value: "all", label: "All verticals" },
  { value: "AI Infrastructure & Compute", label: "AI Infra & Compute" },
  { value: "AI Applications & Tooling", label: "AI Apps & Tooling" },
  { value: "B2B SaaS / Enterprise", label: "B2B SaaS" },
  { value: "Climate & Energy Tech", label: "Climate & Energy" },
  { value: "Consumer / E-commerce & Marketplaces", label: "Consumer / E-com" },
  { value: "Cybersecurity", label: "Cybersecurity" },
  { value: "Data Infrastructure", label: "Data Infra" },
  { value: "Developer Tools", label: "Dev Tools" },
  { value: "Education Tech", label: "EdTech" },
  { value: "Fintech, Payments and Crypto", label: "Fintech" },
  { value: "Healthcare, BioTech & Life Sciences", label: "Healthcare / Bio" },
  { value: "HR Tech / Workforce Tech", label: "HR Tech" },
  { value: "Industrial / IoT / Robotics", label: "Industrial / IoT" },
  { value: "Legal Tech", label: "Legal Tech" },
  { value: "Aero / Defence / Space", label: "Aero / Defence" },
  { value: "PropTech / Real Estate Tech", label: "PropTech" },
  { value: "Construction Tech / AEC", label: "Construction Tech" },
];
const PER_PAGE = 50;

export default function CompaniesPage() {
  const [rows, setRows]       = useState<StartupRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch]   = useState("");
  const [cloud, setCloud]     = useState("all");
  const [ai, setAi]           = useState("all");
  const [vertical, setVertical] = useState("all");
  const [propensity, setPropensity] = useState("all");
  const [tier, setTier] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo]     = useState("");
  const [page, setPage]       = useState(1);
  const [fundingSort, setFundingSort] = useState<"none" | "asc" | "desc">("none");

  function cycleFundingSort() {
    setFundingSort((s) => s === "none" ? "desc" : s === "desc" ? "asc" : "none");
  }

  const sortedRows = fundingSort === "none" ? rows : [...rows].sort((a, b) => {
    const av = a.funding_amount_usd ?? -1;
    const bv = b.funding_amount_usd ?? -1;
    return fundingSort === "desc" ? bv - av : av - bv;
  });

  async function load() {
    setLoading(true);
    try {
      setRows(await getStartups({
        search: search || undefined,
        cloud_provider: cloud === "all" ? undefined : cloud,
        ai_provider: ai === "all" ? undefined : ai,
        vertical: vertical === "all" ? undefined : vertical,
        cloud_propensity: propensity === "all" ? undefined : propensity,
        engagement_tier: tier === "all" ? undefined : tier,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        page,
        per_page: PER_PAGE,
      }));
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [search, cloud, ai, vertical, propensity, tier, dateFrom, dateTo, page]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Companies</h1>
          <p className="text-sm text-gray-500 mt-1">{rows.length} results</p>
        </div>
        <Link href="/add"><Button size="sm">+ Add Company</Button></Link>
      </div>

      <div className="flex flex-wrap gap-3 items-center">
        <Input placeholder="Search company..." value={search} onChange={(e) => setSearch(e.target.value)} className="w-56" />
        <Select value={cloud} onValueChange={(v) => { setCloud(v); setPage(1); }}>
          <SelectTrigger className="w-36"><SelectValue placeholder="Cloud" /></SelectTrigger>
          <SelectContent>{CLOUD_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
        </Select>
        <Select value={ai} onValueChange={(v) => { setAi(v); setPage(1); }}>
          <SelectTrigger className="w-40"><SelectValue placeholder="AI provider" /></SelectTrigger>
          <SelectContent>{AI_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
        </Select>
        <Select value={vertical} onValueChange={(v) => { setVertical(v); setPage(1); }}>
          <SelectTrigger className="w-44"><SelectValue placeholder="Vertical" /></SelectTrigger>
          <SelectContent>{VERTICAL_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
        </Select>
        <Select value={propensity} onValueChange={(v) => { setPropensity(v); setPage(1); }}>
          <SelectTrigger className="w-40"><SelectValue placeholder="Propensity" /></SelectTrigger>
          <SelectContent>{PROPENSITY_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
        </Select>
        <Select value={tier} onValueChange={(v) => { setTier(v); setPage(1); }}>
          <SelectTrigger className="w-40"><SelectValue placeholder="Tier" /></SelectTrigger>
          <SelectContent>{TIER_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
        </Select>
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-gray-500 whitespace-nowrap">Updated</span>
          <Input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} className="w-36 text-sm" />
          <span className="text-sm text-gray-400">–</span>
          <Input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1); }} className="w-36 text-sm" />
        </div>
        <Button variant="ghost" size="sm" onClick={() => { setSearch(""); setCloud("all"); setAi("all"); setVertical("all"); setPropensity("all"); setTier("all"); setDateFrom(""); setDateTo(""); setPage(1); }}>Clear</Button>
      </div>

      <div className="flex gap-2 justify-end">
        <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
        <span className="text-sm text-gray-500 self-center">Page {page}</span>
        <Button variant="outline" size="sm" disabled={rows.length < PER_PAGE} onClick={() => setPage((p) => p + 1)}>Next</Button>
      </div>

      <div className="rounded-lg border bg-white overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Company</TableHead>
              <TableHead>Website</TableHead>
              <TableHead>Cloud</TableHead>
              <TableHead className="w-32"><Tooltip text="How certain we are about the cloud provider attribution" position="below">Conf</Tooltip></TableHead>
              <TableHead><Tooltip text="How deeply integrated the provider is, based on signal strength and diversity" position="below">Entrenchment</Tooltip></TableHead>
              <TableHead>AI Provider</TableHead>
              <TableHead className="w-32"><Tooltip text="How certain we are about the AI provider attribution" position="below">Conf</Tooltip></TableHead>
              <TableHead><Tooltip text="Industry vertical classification" position="below">Vertical</Tooltip></TableHead>
              <TableHead><Tooltip text="Sub-vertical within the industry vertical" position="below">Sub-Vertical</Tooltip></TableHead>
              <TableHead><Tooltip text="Structural likelihood of becoming a significant cloud customer" position="below">Propensity</Tooltip></TableHead>
              <TableHead><Tooltip text="Engagement priority based on funding recency, propensity, and entrenchment" position="below">Tier</Tooltip></TableHead>
              <TableHead>
                <button onClick={cycleFundingSort} className="flex items-center gap-1 hover:text-gray-900 transition-colors group">
                  <Tooltip text="Largest known funding round" position="below">Funding</Tooltip>
                  <span className="text-gray-400 group-hover:text-gray-600 text-xs">
                    {fundingSort === "desc" ? "↓" : fundingSort === "asc" ? "↑" : "↕"}
                  </span>
                </button>
              </TableHead>
              <TableHead><Tooltip text="Month the funding was announced" position="below">Announced</Tooltip></TableHead>
              <TableHead>Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading
              ? Array.from({ length: 8 }).map((_, i) => (
                  <TableRow key={i}>{Array.from({ length: 14 }).map((_, j) => <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>)}</TableRow>
                ))
              : sortedRows.map((r) => (
                  <TableRow key={r.id} className="hover:bg-gray-50">
                    <TableCell className="font-medium">
                      <Link href={`/companies/${r.id}`} className="hover:underline text-blue-700">{r.canonical_name}</Link>
                    </TableCell>
                    <TableCell className="text-gray-500 text-sm">
                      <a href={`https://${r.website}`} target="_blank" rel="noopener" className="hover:underline">{r.website}</a>
                    </TableCell>
                    <TableCell className="max-w-[200px] whitespace-normal">
                      <ProviderBadge name={r.cloud_primary_provider} isMulti={r.cloud_is_multi} providers={r.cloud_providers} isNotApplicable={r.cloud_not_applicable} type="cloud" className="w-full" />
                    </TableCell>
                    <TableCell><ConfidenceBar value={r.cloud_confidence} isNotApplicable={r.cloud_not_applicable} /></TableCell>
                    <TableCell><EntrenchmentChip level={r.cloud_entrenchment} /></TableCell>
                    <TableCell className="max-w-[220px] whitespace-normal">
                      <ProviderBadge name={r.ai_primary_provider} isMulti={r.ai_is_multi} providers={r.ai_providers} isNotApplicable={r.ai_not_applicable} type="ai" className="w-full" />
                    </TableCell>
                    <TableCell><ConfidenceBar value={r.ai_confidence} isNotApplicable={r.ai_not_applicable} /></TableCell>
                    <TableCell className="text-gray-500 text-sm max-w-[160px] whitespace-normal">{r.vertical ?? "—"}</TableCell>
                    <TableCell className="text-gray-500 text-xs max-w-[180px] whitespace-normal">{r.sub_vertical ?? "—"}</TableCell>
                    <TableCell><PropensityChip propensity={r.cloud_propensity} /></TableCell>
                    <TableCell><EngagementTierChip tier={r.engagement_tier} rationale={r.engagement_tier_rationale} /></TableCell>
                    <TableCell className="text-gray-500 text-sm">
                      {r.funding_amount_usd != null ? `$${r.funding_amount_usd}M` : "—"}
                    </TableCell>
                    <TableCell className="text-gray-400 text-xs whitespace-nowrap">
                      {r.funding_announcement_date
                        ? new Date(r.funding_announcement_date).toLocaleDateString("en-US", { month: "short", year: "numeric" })
                        : "—"}
                    </TableCell>
                    <TableCell className="text-gray-400 text-xs">{r.snapshot_date ?? "—"}</TableCell>
                  </TableRow>
                ))
            }
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
