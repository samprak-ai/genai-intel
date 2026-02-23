"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { listPipelineRuns, getPipelineStatus, triggerPipeline, PipelineRun } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";

const STATUS_COLORS: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-700 border-emerald-200",
  running:   "bg-blue-100 text-blue-700 border-blue-200",
  failed:    "bg-red-100 text-red-700 border-red-200",
};

export default function RunsPage() {
  const [runs, setRuns]           = useState<PipelineRun[]>([]);
  const [status, setStatus]       = useState<any>(null);
  const [loading, setLoading]     = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [dryRun, setDryRun]       = useState(false);

  async function load() {
    const [r, s] = await Promise.all([listPipelineRuns(30), getPipelineStatus()]);
    setRuns(r); setStatus(s); setLoading(false);
  }

  useEffect(() => {
    load();
    const interval = setInterval(load, 10_000);
    return () => clearInterval(interval);
  }, []);

  async function handleTrigger() {
    if (!confirm(`Start a ${dryRun ? "DRY RUN" : "LIVE"} pipeline run?`)) return;
    setTriggering(true);
    try { await triggerPipeline({ days_back: 7, dry_run: dryRun }); await load(); }
    catch (err: any) { alert(err.message); }
    finally { setTriggering(false); }
  }

  const fmt = (s?: number) => !s ? "—" : s >= 60 ? `${Math.floor(s/60)}m ${s%60}s` : `${s}s`;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Pipeline Runs</h1>
          <p className="text-sm text-gray-500 mt-1">Weekly discovery → attribution runs. Scheduled every Monday 06:00 UTC.</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer select-none">
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} className="rounded" />
            Dry run
          </label>
          <Button size="sm" disabled={triggering || status?.is_running} onClick={handleTrigger}>
            {status?.is_running ? "Running…" : triggering ? "Starting…" : "▶ Trigger Run"}
          </Button>
        </div>
      </div>

      {status?.is_running && (
        <Card className="border-blue-200 bg-blue-50">
          <CardContent className="pt-4 pb-3">
            <p className="text-sm text-blue-800 font-medium">
              ⚡ Pipeline is currently running
              {status.run_id && <Link href={`/runs/${status.run_id}`} className="ml-2 underline">View logs →</Link>}
            </p>
          </CardContent>
        </Card>
      )}

      <div className="rounded-lg border bg-white overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead><TableHead>Status</TableHead><TableHead>Discovered</TableHead>
              <TableHead>Attributed</TableHead><TableHead>Errors</TableHead><TableHead>Duration</TableHead>
              <TableHead>Started</TableHead><TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => (
                  <TableRow key={i}>{Array.from({ length: 8 }).map((_, j) => <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>)}</TableRow>
                ))
              : runs.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">{r.run_date}</TableCell>
                    <TableCell><Badge variant="outline" className={STATUS_COLORS[r.status] ?? ""}>{r.status}</Badge></TableCell>
                    <TableCell>{r.startups_discovered}</TableCell>
                    <TableCell>{r.startups_attributed}</TableCell>
                    <TableCell>{r.errors_count > 0 ? <span className="text-red-600 font-medium">{r.errors_count}</span> : <span className="text-gray-400">0</span>}</TableCell>
                    <TableCell>{fmt(r.execution_time_seconds)}</TableCell>
                    <TableCell className="text-gray-500 text-sm">{new Date(r.started_at).toLocaleString()}</TableCell>
                    <TableCell><Link href={`/runs/${r.id}`} className="text-xs text-blue-600 hover:underline">Logs →</Link></TableCell>
                  </TableRow>
                ))
            }
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
