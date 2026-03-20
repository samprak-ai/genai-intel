/**
 * Typed API client — wraps fetch calls to the FastAPI backend.
 * All functions throw on non-2xx responses.
 *
 * Auth: reads the `dashboard_token` cookie and forwards it as a Bearer token.
 * Works in both server components (next/headers) and client components (document.cookie).
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Read the dashboard_token from the appropriate cookie store. */
async function getAuthHeader(): Promise<Record<string, string>> {
  // Server-side (React Server Components, Route Handlers)
  if (typeof window === "undefined") {
    try {
      // Dynamic import so client bundles are not affected
      const { cookies } = await import("next/headers");
      const store = await cookies();
      const token = store.get("dashboard_token")?.value;
      if (token) return { Authorization: `Bearer ${token}` };
    } catch {
      // next/headers unavailable in non-Next contexts (e.g. tests) — ignore
    }
    return {};
  }

  // Client-side
  const match = document.cookie.match(/(?:^|;\s*)dashboard_token=([^;]+)/);
  const token = match?.[1];
  if (token) return { Authorization: `Bearer ${token}` };
  return {};
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const authHeader = await getAuthHeader();

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader,
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StartupRow {
  id: string;
  canonical_name: string;
  website: string;
  industry?: string;
  description?: string;
  lead_investors?: string[];
  founder_background?: string[];
  // Cloud
  cloud_is_multi?: boolean;
  cloud_primary_provider?: string;
  cloud_providers?: string[];
  cloud_confidence?: number;
  cloud_entrenchment?: string;
  cloud_evidence_count?: number;
  cloud_not_applicable?: boolean;
  cloud_not_applicable_note?: string;
  // AI
  ai_is_multi?: boolean;
  ai_primary_provider?: string;
  ai_providers?: string[];
  ai_confidence?: number;
  ai_entrenchment?: string;
  ai_evidence_count?: number;
  ai_not_applicable?: boolean;
  ai_not_applicable_note?: string;
  snapshot_date?: string;
  // Funding (only present for pipeline-discovered companies)
  funding_amount_usd?: number;
  funding_announcement_date?: string;
  // Classification
  vertical?: string;
  sub_vertical?: string;
  cloud_propensity?: "High" | "Medium" | "Low";
  classification_confidence?: "high" | "medium" | "low";
  classification_source?: string;
  // Engagement tier
  engagement_tier?: number;
  engagement_tier_label?: string;
  engagement_tier_rationale?: string;
  // Triggers
  active_trigger_count?: number;
  // Outreach intelligence
  engagement_timing?: "Hot" | "Warm" | "Watch";
  recommended_angle?: string;
  key_signals?: string[];
  intelligence_generated_at?: string;
}

export interface Trigger {
  id: string;
  company_id: string;
  trigger_type: string;
  trigger_label: string;
  signal_strength: "strong" | "moderate" | "weak";
  source_url?: string;
  detected_date: string;
  created_at: string;
}

export interface Signal {
  id: string;
  provider_type: "cloud" | "ai";
  provider_name: string;
  signal_source: string;
  signal_strength: "STRONG" | "MEDIUM" | "WEAK";
  evidence_text?: string;
  evidence_url?: string;
  confidence_weight: number;
  collected_at: string;
}

export interface PipelineRun {
  id: string;
  run_date: string;
  status: "running" | "completed" | "failed";
  startups_discovered: number;
  startups_attributed: number;
  errors_count: number;
  execution_time_seconds?: number;
  started_at: string;
  completed_at?: string;
}

export interface ProviderDistribution {
  provider: string;
  startup_count: number;
  multi_cloud_count: number;
  sole_provider_count: number;
  avg_confidence: number;
}

export interface VerticalDistribution {
  vertical: string;
  count: number;
}

export interface Summary {
  total_companies: number;
  cloud_distribution: ProviderDistribution[];
  ai_distribution: ProviderDistribution[];
  vertical_distribution?: VerticalDistribution[];
  latest_run?: PipelineRun;
  tier_1_count?: number;
  active_trigger_count?: number;
}

// ---------------------------------------------------------------------------
// Startups
// ---------------------------------------------------------------------------

export const getStartups = (params?: {
  cloud_provider?: string;
  ai_provider?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  vertical?: string;
  cloud_propensity?: string;
  engagement_tier?: string;
  page?: number;
  per_page?: number;
}) => {
  const qs = new URLSearchParams();
  if (params?.cloud_provider) qs.set("cloud_provider", params.cloud_provider);
  if (params?.ai_provider) qs.set("ai_provider", params.ai_provider);
  if (params?.search) qs.set("search", params.search);
  if (params?.date_from) qs.set("date_from", params.date_from);
  if (params?.date_to) qs.set("date_to", params.date_to);
  if (params?.vertical) qs.set("vertical", params.vertical);
  if (params?.cloud_propensity) qs.set("cloud_propensity", params.cloud_propensity);
  if (params?.engagement_tier) qs.set("engagement_tier", params.engagement_tier);
  if (params?.page) qs.set("page", String(params.page));
  if (params?.per_page) qs.set("per_page", String(params.per_page));
  const query = qs.toString() ? `?${qs}` : "";
  return apiFetch<StartupRow[]>(`/api/startups${query}`);
};

export const getStartup = (id: string) =>
  apiFetch<{
    startup: StartupRow;
    snapshot: Record<string, unknown> | null;
    signals: Signal[];
    funding_events: Record<string, unknown>[];
    manual_override: Record<string, unknown> | null;
    snapshot_history: Record<string, unknown>[];
    triggers: Trigger[];
  }>(`/api/startups/${id}`);

export const createStartup = (body: {
  company_name: string;
  website: string;
  evidence_urls?: string[];
  lead_investors?: string[];
  founder_background?: string[];
  notes?: string;
}) => apiFetch<{ startup: StartupRow }>(`/api/startups`, {
  method: "POST",
  body: JSON.stringify(body),
});

export const patchStartup = (
  id: string,
  body: {
    evidence_urls?: string[];
    lead_investors?: string[];
    founder_background?: string[];
    notes?: string;
  }
) => apiFetch<unknown>(`/api/startups/${id}`, {
  method: "PATCH",
  body: JSON.stringify(body),
});

export const reAttribute = (id: string) =>
  apiFetch<unknown>(`/api/startups/${id}/re-attribute`, { method: "POST" });

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------

export interface SearchUsageDay {
  usage_date: string;
  [source: string]: string | number;  // dynamic source keys + usage_date
}

export interface SearchUsage {
  daily: SearchUsageDay[];
  sources: string[];
  totals: Record<string, number>;
  total_queries: number;
  estimated_cost_usd: number;
}

export const getSummary = () => apiFetch<Summary>("/api/analytics/summary");
export const getSearchUsage = (days = 30) =>
  apiFetch<SearchUsage>(`/api/analytics/search-usage?days=${days}`);
export const getCloudDistribution = () => apiFetch<ProviderDistribution[]>("/api/analytics/cloud-distribution");
export const getAIDistribution = () => apiFetch<ProviderDistribution[]>("/api/analytics/ai-distribution");
export const getRecentFunding = (limit = 20) =>
  apiFetch<Record<string, unknown>[]>(`/api/analytics/recent-funding?limit=${limit}`);
export const getProviderChanges = () =>
  apiFetch<Record<string, unknown>[]>("/api/analytics/provider-changes");

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

export const getPipelineStatus = () =>
  apiFetch<{ is_running: boolean; run_id?: string; started_at?: string }>("/api/pipeline/status");

export const listPipelineRuns = (limit = 20) =>
  apiFetch<PipelineRun[]>(`/api/pipeline/runs?limit=${limit}`);

export const getPipelineRun = (id: string, params?: { stage?: string; level?: string }) => {
  const qs = new URLSearchParams();
  if (params?.stage) qs.set("stage", params.stage);
  if (params?.level) qs.set("level", params.level);
  const query = qs.toString() ? `?${qs}` : "";
  return apiFetch<{ run: PipelineRun; logs: Record<string, unknown>[] }>(`/api/pipeline/runs/${id}${query}`);
};

export const triggerPipeline = (body: {
  days_back?: number;
  limit?: number;
  dry_run?: boolean;
}) => apiFetch<{ message: string }>("/api/pipeline/trigger", {
  method: "POST",
  body: JSON.stringify(body),
});
