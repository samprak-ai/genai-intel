"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { getStartup, patchStartup, reAttribute, Signal } from "@/lib/api";
import { ProviderBadge } from "@/components/ProviderBadge";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { EntrenchmentChip } from "@/components/EntrenchmentChip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";

const STRENGTH_COLORS: Record<string, string> = {
  STRONG: "bg-emerald-100 text-emerald-700",
  MEDIUM: "bg-amber-100   text-amber-700",
  WEAK:   "bg-gray-100    text-gray-600",
};

export default function CompanyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [data, setData]             = useState<any>(null);
  const [loading, setLoading]       = useState(true);
  const [saving, setSaving]         = useState(false);
  const [reAttribBusy, setReAttribBusy] = useState(false);
  const [evUrls, setEvUrls]         = useState("");
  const [investors, setInvestors]   = useState("");
  const [founders, setFounders]     = useState("");
  const [notes, setNotes]           = useState("");

  async function load() {
    setLoading(true);
    try {
      const d = await getStartup(id);
      setData(d);
      const ov = d.manual_override as any;
      if (ov) {
        setEvUrls((ov.evidence_urls ?? []).join("\n"));
        setInvestors((ov.lead_investors ?? []).join(", "));
        setFounders((ov.founder_background ?? []).join(", "));
        setNotes(ov.notes ?? "");
      }
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [id]);

  async function handleSave() {
    setSaving(true);
    try {
      await patchStartup(id, {
        evidence_urls: evUrls.split("\n").map((u) => u.trim()).filter(Boolean),
        lead_investors: investors.split(",").map((v) => v.trim()).filter(Boolean),
        founder_background: founders.split(",").map((v) => v.trim()).filter(Boolean),
        notes: notes || undefined,
      });
      await load();
    } finally { setSaving(false); }
  }

  async function handleReAttribute() {
    setReAttribBusy(true);
    try { await reAttribute(id); await load(); }
    finally { setReAttribBusy(false); }
  }

  if (loading && !data) return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-48" /><Skeleton className="h-32 w-full" /><Skeleton className="h-64 w-full" />
    </div>
  );
  if (!data) return <p className="text-red-500">Company not found.</p>;

  const { startup, snapshot, signals, funding_events } = data;
  const cloudSignals = (signals as Signal[]).filter((s) => s.provider_type === "cloud");
  const aiSignals    = (signals as Signal[]).filter((s) => s.provider_type === "ai");

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">{startup.canonical_name}</h1>
          <a href={`https://${startup.website}`} target="_blank" rel="noopener" className="text-sm text-blue-600 hover:underline">{startup.website}</a>
          {startup.description && <p className="text-sm text-gray-500 mt-1 max-w-xl">{startup.description}</p>}
        </div>
        <Button variant="outline" size="sm" onClick={() => router.back()}>← Back</Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <AttributionCard title="☁️ Cloud" provider={snapshot?.cloud_primary_provider} isMulti={snapshot?.cloud_is_multi} providers={snapshot?.cloud_providers} isNA={snapshot?.cloud_not_applicable} naNote={snapshot?.cloud_not_applicable_note} confidence={snapshot?.cloud_confidence} entrenchment={snapshot?.cloud_entrenchment} evidenceCount={snapshot?.cloud_evidence_count} type="cloud" signals={cloudSignals} />
        <AttributionCard title="🤖 AI Provider" provider={snapshot?.ai_primary_provider} isMulti={snapshot?.ai_is_multi} providers={snapshot?.ai_providers} isNA={snapshot?.ai_not_applicable} naNote={snapshot?.ai_not_applicable_note} confidence={snapshot?.ai_confidence} entrenchment={snapshot?.ai_entrenchment} evidenceCount={snapshot?.ai_evidence_count} type="ai" signals={aiSignals} />
      </div>

      <Card>
        <CardHeader className="pb-3"><CardTitle className="text-base">Manual Enrichment</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div>
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide block mb-1">Evidence URLs (one per line)</label>
            <textarea className="w-full border rounded-md px-3 py-2 text-sm font-mono min-h-[80px] focus:outline-none focus:ring-2 focus:ring-blue-500" value={evUrls} onChange={(e) => setEvUrls(e.target.value)} placeholder="https://example.com/aws-partnership" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-medium text-gray-500 uppercase tracking-wide block mb-1">Lead Investors (comma-separated)</label>
              <Input value={investors} onChange={(e) => setInvestors(e.target.value)} placeholder="GV, Sequoia, a16z" />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 uppercase tracking-wide block mb-1">Founder Backgrounds (comma-separated)</label>
              <Input value={founders} onChange={(e) => setFounders(e.target.value)} placeholder="Google Brain, DeepMind" />
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide block mb-1">Notes</label>
            <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Any additional context..." />
          </div>
          <div className="flex gap-3 pt-1">
            <Button size="sm" onClick={handleSave} disabled={saving}>{saving ? "Saving…" : "Save Enrichment"}</Button>
            <Button size="sm" variant="outline" onClick={handleReAttribute} disabled={reAttribBusy}>{reAttribBusy ? "Running…" : "Re-attribute Now"}</Button>
          </div>
        </CardContent>
      </Card>

      {funding_events.length > 0 && (
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Funding History</CardTitle></CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead><TableHead>Round</TableHead><TableHead>Amount</TableHead><TableHead>Investors</TableHead><TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {funding_events.map((f: any) => (
                  <TableRow key={f.id}>
                    <TableCell className="text-sm">{f.announcement_date}</TableCell>
                    <TableCell>{f.funding_round}</TableCell>
                    <TableCell>${f.funding_amount_usd}M</TableCell>
                    <TableCell className="text-sm text-gray-600">{(f.lead_investors ?? []).join(", ") || "—"}</TableCell>
                    <TableCell><a href={f.source_url} target="_blank" rel="noopener" className="text-xs text-blue-600 hover:underline">{f.source_name}</a></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function AttributionCard({ title, provider, isMulti, providers, isNA, naNote, confidence, entrenchment, evidenceCount, type, signals }: any) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle className="text-sm font-medium text-gray-500">{title}</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <ProviderBadge name={provider} isMulti={isMulti} providers={providers} isNotApplicable={isNA} type={type} className="text-sm" />
        {isNA && naNote && <p className="text-xs text-gray-400 italic">{naNote}</p>}
        {!isNA && (
          <>
            <ConfidenceBar value={confidence} />
            <div className="flex items-center gap-2">
              <EntrenchmentChip level={entrenchment} />
              {evidenceCount != null && <span className="text-xs text-gray-400">{evidenceCount} signals</span>}
            </div>
          </>
        )}
        {signals.length > 0 && (
          <div>
            <button className="text-xs text-blue-600 hover:underline" onClick={() => setExpanded((v: boolean) => !v)}>
              {expanded ? "Hide signals" : `Show ${signals.length} signal${signals.length !== 1 ? "s" : ""}`}
            </button>
            {expanded && (
              <div className="mt-2 space-y-2">
                {signals.map((s: Signal) => (
                  <div key={s.id} className="rounded border bg-gray-50 p-2 text-xs space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded font-medium ${STRENGTH_COLORS[s.signal_strength] ?? ""}`}>{s.signal_strength}</span>
                      <span className="text-gray-500">{s.signal_source}</span>
                      <span className="text-gray-400 ml-auto">{s.provider_name}</span>
                    </div>
                    {s.evidence_text && <p className="text-gray-600 line-clamp-2">{s.evidence_text}</p>}
                    {s.evidence_url && <a href={s.evidence_url} target="_blank" rel="noopener" className="text-blue-500 hover:underline truncate block">{s.evidence_url}</a>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
