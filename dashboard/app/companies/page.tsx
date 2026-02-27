"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { getStartups, StartupRow } from "@/lib/api";
import { ProviderBadge } from "@/components/ProviderBadge";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { EntrenchmentChip } from "@/components/EntrenchmentChip";
import { Tooltip } from "@/components/Tooltip";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";

const CLOUD_OPTIONS = [{ value: "all", label: "All clouds" }, { value: "AWS", label: "AWS" }, { value: "GCP", label: "GCP" }, { value: "Azure", label: "Azure" }, { value: "CoreWeave", label: "CoreWeave" }];
const AI_OPTIONS    = [{ value: "all", label: "All AI" }, { value: "Anthropic", label: "Anthropic" }, { value: "OpenAI", label: "OpenAI" }, { value: "Google AI", label: "Google AI" }, { value: "Cohere", label: "Cohere" }, { value: "Mistral", label: "Mistral" }];
const PER_PAGE = 50;

export default function CompaniesPage() {
  const [rows, setRows]       = useState<StartupRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch]   = useState("");
  const [cloud, setCloud]     = useState("all");
  const [ai, setAi]           = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo]     = useState("");
  const [page, setPage]       = useState(1);

  async function load() {
    setLoading(true);
    try {
      setRows(await getStartups({
        search: search || undefined,
        cloud_provider: cloud === "all" ? undefined : cloud,
        ai_provider: ai === "all" ? undefined : ai,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        page,
        per_page: PER_PAGE,
      }));
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [search, cloud, ai, dateFrom, dateTo, page]);

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
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-gray-500 whitespace-nowrap">Updated</span>
          <Input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} className="w-36 text-sm" />
          <span className="text-sm text-gray-400">–</span>
          <Input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1); }} className="w-36 text-sm" />
        </div>
        <Button variant="ghost" size="sm" onClick={() => { setSearch(""); setCloud("all"); setAi("all"); setDateFrom(""); setDateTo(""); setPage(1); }}>Clear</Button>
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
              <TableHead><Tooltip text="Largest known funding round" position="below">Funding</Tooltip></TableHead>
              <TableHead><Tooltip text="Month the funding was announced" position="below">Announced</Tooltip></TableHead>
              <TableHead>Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading
              ? Array.from({ length: 8 }).map((_, i) => (
                  <TableRow key={i}>{Array.from({ length: 10 }).map((_, j) => <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>)}</TableRow>
                ))
              : rows.map((r) => (
                  <TableRow key={r.id} className="hover:bg-gray-50">
                    <TableCell className="font-medium">
                      <Link href={`/companies/${r.id}`} className="hover:underline text-blue-700">{r.canonical_name}</Link>
                    </TableCell>
                    <TableCell className="text-gray-500 text-sm">
                      <a href={`https://${r.website}`} target="_blank" rel="noopener" className="hover:underline">{r.website}</a>
                    </TableCell>
                    <TableCell className="max-w-[160px]">
                      <ProviderBadge name={r.cloud_primary_provider} isMulti={r.cloud_is_multi} providers={r.cloud_providers} isNotApplicable={r.cloud_not_applicable} type="cloud" className="whitespace-normal w-full h-auto !rounded-md" />
                    </TableCell>
                    <TableCell><ConfidenceBar value={r.cloud_confidence} isNotApplicable={r.cloud_not_applicable} /></TableCell>
                    <TableCell><EntrenchmentChip level={r.cloud_entrenchment} /></TableCell>
                    <TableCell className="max-w-[160px]">
                      <ProviderBadge name={r.ai_primary_provider} isMulti={r.ai_is_multi} providers={r.ai_providers} isNotApplicable={r.ai_not_applicable} type="ai" className="whitespace-normal w-full h-auto !rounded-md" />
                    </TableCell>
                    <TableCell><ConfidenceBar value={r.ai_confidence} isNotApplicable={r.ai_not_applicable} /></TableCell>
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
