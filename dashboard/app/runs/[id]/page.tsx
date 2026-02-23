"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { getPipelineRun, PipelineRun } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const LEVEL_COLORS: Record<string, string> = {
  info:  "bg-blue-50 text-blue-700 border-blue-100",
  warn:  "bg-amber-50 text-amber-700 border-amber-100",
  error: "bg-red-50 text-red-700 border-red-100",
};
const STATUS_COLORS: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-700",
  running:   "bg-blue-100 text-blue-700",
  failed:    "bg-red-100 text-red-700",
};

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [data, setData]     = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [stage, setStage]   = useState("");
  const [level, setLevel]   = useState("");

  async function load() {
    setLoading(true);
    try { setData(await getPipelineRun(id, { stage: stage || undefined, level: level || undefined })); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [id, stage, level]);

  if (loading && !data) return (
    <div className="space-y-4"><Skeleton className="h-8 w-48" /><Skeleton className="h-24 w-full" /><Skeleton className="h-64 w-full" /></div>
  );

  const run  = data?.run as PipelineRun;
  const logs = data?.logs ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Run — {run?.run_date}</h1>
          <div className="flex items-center gap-2 mt-1">
            <Badge variant="outline" className={STATUS_COLORS[run?.status ?? ""] ?? ""}>{run?.status}</Badge>
            <span className="text-sm text-gray-500">{run?.startups_discovered} discovered · {run?.startups_attributed} attributed · {run?.errors_count} errors</span>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={() => router.back()}>← Back</Button>
      </div>

      <div className="flex gap-3">
        <Select value={stage} onValueChange={setStage}>
          <SelectTrigger className="w-40"><SelectValue placeholder="All stages" /></SelectTrigger>
          <SelectContent>{["", "discovery", "resolution", "attribution", "storage"].map((s) => <SelectItem key={s} value={s}>{s || "All stages"}</SelectItem>)}</SelectContent>
        </Select>
        <Select value={level} onValueChange={setLevel}>
          <SelectTrigger className="w-36"><SelectValue placeholder="All levels" /></SelectTrigger>
          <SelectContent>{["", "info", "warn", "error"].map((l) => <SelectItem key={l} value={l}>{l || "All levels"}</SelectItem>)}</SelectContent>
        </Select>
        <span className="text-sm text-gray-400 self-center">{logs.length} entries</span>
      </div>

      <div className="space-y-1.5">
        {logs.length === 0
          ? <p className="text-sm text-gray-400 py-8 text-center">No logs found</p>
          : logs.map((log: any) => (
              <div key={log.id} className={`rounded border px-3 py-2 text-xs flex gap-3 items-start ${LEVEL_COLORS[log.level] ?? ""}`}>
                <span className="font-mono text-gray-400 whitespace-nowrap mt-0.5 w-20 shrink-0">{new Date(log.created_at).toLocaleTimeString()}</span>
                <span className="uppercase font-semibold w-16 shrink-0">{log.stage}</span>
                <span className="flex-1">{log.message}</span>
                {log.detail && Object.keys(log.detail).length > 0 && (
                  <details className="shrink-0">
                    <summary className="cursor-pointer text-gray-400 hover:text-gray-600">detail</summary>
                    <pre className="mt-1 text-xs overflow-auto max-w-xs">{JSON.stringify(log.detail, null, 2)}</pre>
                  </details>
                )}
              </div>
            ))
        }
      </div>
    </div>
  );
}
