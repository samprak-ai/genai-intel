"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { createStartup } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function AddCompanyPage() {
  const router = useRouter();
  const [companyName, setCompanyName] = useState("");
  const [website, setWebsite]         = useState("");
  const [evUrls, setEvUrls]           = useState("");
  const [investors, setInvestors]     = useState("");
  const [founders, setFounders]       = useState("");
  const [notes, setNotes]             = useState("");
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!companyName.trim() || !website.trim()) return;
    setLoading(true); setError("");
    try {
      const result = await createStartup({
        company_name: companyName.trim(),
        website: website.trim().replace(/^https?:\/\//, "").replace(/\/$/, ""),
        evidence_urls: evUrls.split("\n").map((u) => u.trim()).filter(Boolean),
        lead_investors: investors.split(",").map((v) => v.trim()).filter(Boolean),
        founder_background: founders.split(",").map((v) => v.trim()).filter(Boolean),
        notes: notes.trim() || undefined,
      });
      router.push(`/companies/${result.startup.id}`);
    } catch (err: any) {
      setError(err.message ?? "Something went wrong");
      setLoading(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Add Company</h1>
        <p className="text-sm text-gray-500 mt-1">Attribution runs automatically after submission. Add enrichment data to improve accuracy.</p>
      </div>
      <Card>
        <CardHeader className="pb-3"><CardTitle className="text-base">Company Details</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Company Name *</label>
                <Input value={companyName} onChange={(e) => setCompanyName(e.target.value)} placeholder="Acme AI" required />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Website *</label>
                <Input value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="acme.ai" required />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Evidence URLs <span className="normal-case font-normal">(one per line — Tier 1 signals)</span></label>
              <textarea className="w-full border rounded-md px-3 py-2 text-sm font-mono min-h-[80px] focus:outline-none focus:ring-2 focus:ring-blue-500" value={evUrls} onChange={(e) => setEvUrls(e.target.value)} placeholder={"https://acme.ai/blog/aws-partnership\nhttps://aws.amazon.com/marketplace/acme"} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Lead Investors <span className="normal-case font-normal">(comma-separated)</span></label>
                <Input value={investors} onChange={(e) => setInvestors(e.target.value)} placeholder="GV, Sequoia, a16z" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Founder Backgrounds <span className="normal-case font-normal">(comma-separated)</span></label>
                <Input value={founders} onChange={(e) => setFounders(e.target.value)} placeholder="Google Brain, DeepMind" />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Notes</label>
              <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Any additional context..." />
            </div>
            {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">{error}</p>}
            <div className="flex gap-3 pt-1">
              <Button type="submit" disabled={loading}>{loading ? "Running attribution…" : "Add & Attribute"}</Button>
              <Button type="button" variant="outline" onClick={() => router.back()} disabled={loading}>Cancel</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
