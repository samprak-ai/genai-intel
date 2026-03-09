# GenAI-Intel — Technical Infrastructure Reference

> This document describes the full technical stack, deployment infrastructure, and architectural patterns of the GenAI-Intel project. Use it as context when building a new application that shares the same infrastructure.

---

## Stack Overview

| Layer | Technology | Hosting |
|-------|-----------|---------|
| **API** | FastAPI (Python 3.11+) | Railway (`web-production-f93a5.up.railway.app`) |
| **Database** | PostgreSQL 15 via Supabase | Supabase (managed) |
| **Dashboard** | Next.js 16 + React 19 + Tailwind 4 | Vercel |
| **LLM** | Anthropic Claude (Haiku for cheap tasks) | Anthropic API |
| **Search** | Serper.dev (Google Search) + Perplexity Sonar | External APIs |
| **Cron** | cron-job.org → POST to API endpoint | External |

---

## 1. Backend (FastAPI)

### App Setup (`api/main.py`)

```python
app = FastAPI(title="GenAI-Intel API", version="1.0.0")

# CORS — allows localhost:3000, *.vercel.app, and FRONTEND_URL env var
app.add_middleware(CORSMiddleware,
    allow_origins=[...],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth — Bearer token via X-API-Key header
# Disabled when DASHBOARD_SECRET env var is empty (local dev)
```

### Router Pattern (`api/routers/`)

Each router is a separate file with its own prefix:

```python
# api/routers/startups.py
router = APIRouter(prefix="/api/startups", tags=["startups"])

@router.get("")
def list_startups(
    cloud_provider: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: DatabaseClient = Depends(get_db),
):
    return db.list_startups(...)
```

Routers are registered in `main.py`:
```python
app.include_router(startups.router, dependencies=[Depends(verify_token)])
app.include_router(analytics.router, dependencies=[Depends(verify_token)])
app.include_router(pipeline.router, dependencies=[Depends(verify_token)])
```

### Dependency Injection (`api/deps.py`)

```python
from functools import lru_cache

@lru_cache(maxsize=1)
def get_db() -> DatabaseClient:
    """Singleton database client per process"""
    return DatabaseClient()

def verify_token(creds: HTTPAuthorizationCredentials | None = Security(_bearer)) -> None:
    """Bearer-token guard. Returns None if DASHBOARD_SECRET not set (local dev)."""
    secret = os.getenv("DASHBOARD_SECRET")
    if not secret:
        return  # local dev — no auth
    if not creds or creds.credentials != secret:
        raise HTTPException(status_code=401)
```

### Database Client (`app/core/database.py`)

Wraps the Supabase Python SDK with typed methods:

```python
class DatabaseClient:
    def __init__(self):
        self.client: Client = create_client(
            os.getenv('SUPABASE_URL'),
            os.getenv('SUPABASE_KEY'),
        )

    # Pattern: every method wraps a Supabase query
    def list_startups(self, cloud_provider=None, search=None, page=1, per_page=50):
        query = self.client.table('latest_attributions').select('*')
        if cloud_provider:
            query = query.eq('cloud_primary_provider', cloud_provider)
        if search:
            query = query.ilike('canonical_name', f'%{search}%')
        # Pagination
        start = (page - 1) * per_page
        query = query.range(start, start + per_page - 1)
        result = query.execute()
        return result.data or []
```

Key patterns:
- **Upsert with dedup**: `.upsert(data, on_conflict='website')`
- **Count queries**: `.select('id', count='exact')` → `result.count`
- **Ordering**: `.order('created_at', desc=True)`
- **Filtering**: `.eq()`, `.in_()`, `.ilike()`, `.gte()`, `.is_()`

### Background Tasks

FastAPI's `BackgroundTasks` for long-running operations:

```python
@router.post("/trigger", status_code=202)
def trigger_pipeline(body: TriggerRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_pipeline_background, ...)
    return {"message": "Pipeline run started", "status": "accepted"}
```

The background function runs in a separate thread (not async). State tracked via module-level dict:
```python
_active_run: dict = {}  # Tracks current run — cleared in finally block
```

### Cron Integration

External cron (cron-job.org) POSTs to a dedicated endpoint:
```python
# Registered directly on app (bypasses router-level auth)
# Uses its own X-Cron-Secret header for authentication
@app.post("/api/pipeline/cron")
def cron_trigger_handler(request: Request, background_tasks: BackgroundTasks):
    expected = os.getenv("CRON_SECRET")
    provided = request.headers.get("X-Cron-Secret", "")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401)
    background_tasks.add_task(_run_pipeline_background, ...)
```

---

## 2. Database (Supabase / PostgreSQL)

### Connection

```python
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])
```

The Supabase Python SDK communicates via PostgREST (HTTP REST API over PostgreSQL). No direct SQL connections — all queries use the SDK's builder pattern.

### Schema Pattern

Tables use UUID primary keys with `gen_random_uuid()`:

```sql
CREATE TABLE IF NOT EXISTS my_table (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id   UUID NOT NULL REFERENCES parent_table(id) ON DELETE CASCADE,
    some_field  TEXT NOT NULL,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common query patterns
CREATE INDEX idx_my_table_entity_id ON my_table(entity_id);
CREATE UNIQUE INDEX idx_my_table_dedup ON my_table(entity_id, some_field);
```

### Views

Views are used for aggregated/filtered reads. The main pattern:

```sql
-- DISTINCT ON for "latest record per entity"
CREATE VIEW latest_records AS
SELECT DISTINCT ON (e.id)
    e.id, e.name, r.score, r.created_at
FROM entities e
LEFT JOIN records r ON e.id = r.entity_id
ORDER BY e.id, r.created_at DESC NULLS LAST;
```

**Important**: Views must be DROP CASCADE'd and recreated when underlying table columns change. All dependent views must also be recreated. Always end migrations with:
```sql
NOTIFY pgrst, 'reload schema';
```

### Current Tables

| Table | Purpose |
|-------|---------|
| `startups` | Core entity (name, website, industry) |
| `funding_events` | Funding rounds (amount, round, date, investors) |
| `attribution_signals` | Evidence signals (provider, source, weight, URL) |
| `attribution_snapshots` | Point-in-time attribution + classification + engagement |
| `weekly_runs` | Pipeline execution records |
| `pipeline_logs` | Structured logs per run (stage, level, message) |
| `manual_overrides` | User-provided enrichment data |
| `company_triggers` | Detected inflection events |

### Current Views

| View | Purpose |
|------|---------|
| `latest_attributions` | Latest snapshot per startup (≥$10M filter) |
| `cloud_provider_distribution` | Provider market share |
| `ai_provider_distribution` | AI provider market share |
| `recent_funding_with_attribution` | Latest 50 funding events |

---

## 3. Frontend (Next.js + Vercel)

### Stack

```json
{
  "next": "16.1.6",
  "react": "19.2.3",
  "recharts": "^3.7.0",        // Charts
  "lucide-react": "^0.575.0",   // Icons
  "radix-ui": "^1.4.3",         // Accessible primitives
  "tailwindcss": "^4",
  "class-variance-authority": "^0.7.1",
  "clsx": "^2.1.1",
  "tailwind-merge": "^3.5.0"
}
```

### App Structure

```
dashboard/
  app/
    layout.tsx              — Root layout (nav bar, font, body wrapper)
    page.tsx                — Dashboard home (server component, force-dynamic)
    login/page.tsx          — Login form
    companies/
      page.tsx              — Companies table (client component)
      [id]/page.tsx         — Company detail (client component)
    add/page.tsx            — Add company form
    runs/
      page.tsx              — Pipeline runs list
      [id]/page.tsx         — Run detail + structured logs
    ready-to-engage/
      page.tsx              — Tier 1/2 engagement view
    api/auth/login/
      route.ts              — Auth API route (sets cookie)
  components/
    ui/                     — shadcn/ui components (card, table, badge, button, input, select, skeleton)
    ConfidenceBar.tsx       — 0-100 progress bar
    EntrenchmentChip.tsx    — STRONG/MODERATE/WEAK badge
    PropensityChip.tsx      — High/Medium/Low badge
    EngagementTierChip.tsx  — Tier 1/2/3 badge
    EngagementTimingChip.tsx — Hot/Warm/Watch badge
    TriggerBadge.tsx        — Trigger count badge
    ProviderBadge.tsx       — Provider name(s) with multi-provider handling
    DistributionChart.tsx   — Pie chart (Recharts)
    VerticalChart.tsx       — Vertical distribution chart
    Tooltip.tsx             — Hover tooltip
  lib/
    api.ts                  — Typed API client (all fetch functions + interfaces)
    utils.ts                — cn() utility (clsx + tailwind-merge)
  middleware.ts             — Auth middleware (protects routes, redirects to /login)
```

### API Client Pattern (`lib/api.ts`)

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function getAuthHeader(): Promise<Record<string, string>> {
  // Server-side: reads from next/headers cookies
  // Client-side: reads from document.cookie
  return { Authorization: `Bearer ${token}` };
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const authHeader = await getAuthHeader();
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeader },
    ...init,
  });
  if (res.status === 401) {
    // Client-side: redirect to /login
  }
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

// Typed exports
export const getStartups = (params?) => apiFetch<StartupRow[]>(`/api/startups?${qs}`);
export const getSummary  = () => apiFetch<Summary>("/api/analytics/summary");
```

### Auth Flow

1. User visits `/login`, enters bearer token
2. `POST /api/auth/login` route handler validates token against API, sets `dashboard_token` cookie
3. `middleware.ts` checks for cookie on protected routes, redirects to `/login` if missing
4. `apiFetch()` reads cookie and sends as `Authorization: Bearer` header
5. FastAPI `verify_token` dependency validates the token

### Component Pattern (Badge/Chip)

All badge components follow the same pattern:

```tsx
import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/Tooltip";
import { cn } from "@/lib/utils";

const COLORS: Record<string, string> = {
  High: "bg-emerald-100 text-emerald-800 border-emerald-200",
  Low:  "bg-orange-100  text-orange-700  border-orange-200",
};

export function MyChip({ value, className }: { value?: string | null; className?: string }) {
  if (!value) return <span className="text-gray-400 text-xs">—</span>;
  return (
    <Tooltip text="Description for tooltip">
      <Badge variant="outline" className={cn("text-xs font-medium", COLORS[value], className)}>
        {value}
      </Badge>
    </Tooltip>
  );
}
```

### Page Pattern (Client Component with Filters)

```tsx
"use client";
import { useEffect, useState } from "react";
import { getStartups, StartupRow } from "@/lib/api";

export default function MyPage() {
  const [rows, setRows] = useState<StartupRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    setLoading(true);
    getStartups({ my_filter: filter === "all" ? undefined : filter })
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [filter]);

  return (
    <Card>
      {loading ? <Skeleton /> : (
        <Table>...</Table>
      )}
    </Card>
  );
}
```

### Page Pattern (Server Component with KPIs)

```tsx
// No "use client" — runs on server
export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const summary = await getSummary();
  return (
    <div className="grid grid-cols-5 gap-4">
      <KpiCard label="Total" value={String(summary.total)} />
    </div>
  );
}

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <CardContent className="pt-5 pb-4">
        <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">{label}</p>
        <p className="text-2xl font-bold truncate">{value}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </CardContent>
    </Card>
  );
}
```

---

## 4. Deployment

### Railway (API)

- **Procfile**: `web: uvicorn api.main:app --host 0.0.0.0 --port $PORT`
- **Auto-deploy**: Push to `main` branch triggers deploy
- **Health check**: `GET /health` → `{"status": "ok"}`
- **Env vars**: Set in Railway dashboard (see Section 6)

### Vercel (Dashboard)

- **Framework**: Next.js (auto-detected)
- **Root directory**: `dashboard/`
- **Build command**: `npm run build`
- **Env vars**: `NEXT_PUBLIC_API_URL` = Railway API URL

### Supabase (Database)

- **Migrations**: Run `.sql` files manually in Supabase Dashboard > SQL Editor
- **No ORM**: All access via Supabase Python/JS SDK (PostgREST)
- **Schema reloads**: `NOTIFY pgrst, 'reload schema'` after DDL changes

### Cron (cron-job.org)

- **Endpoint**: `POST {RAILWAY_URL}/api/pipeline/cron`
- **Header**: `X-Cron-Secret: {CRON_SECRET}`
- **Schedule**: Configurable (e.g. Mon/Wed/Fri at 6am UTC)

---

## 5. LLM Integration Patterns

### Anthropic Claude (Haiku)

```python
import anthropic

client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
resp = client.messages.create(
    model='claude-3-haiku-20240307',
    max_tokens=600,
    messages=[{'role': 'user', 'content': prompt}],
)
raw = resp.content[0].text.strip()
```

**JSON extraction from LLM response** (LLMs often append prose):
```python
import re, json
json_match = re.search(r'\{[\s\S]*\}', raw)  # First JSON object
if json_match:
    parsed = json.loads(json_match.group())
```

**Graceful degradation**: Always wrap in try/except, return sensible defaults on failure:
```python
try:
    resp = client.messages.create(...)
    # parse response
except Exception as e:
    print(f"⚠️ LLM call failed: {e}")
    return default_result  # Never fail the whole pipeline
```

### Serper.dev (Google Search API)

All search calls go through the shared helper in `app/core/search.py`:

```python
from app.core.search import serper_search, parse_result_age, parse_age_to_strength

# Basic search — returns normalized [{"title", "url", "snippet", "date"}]
results = serper_search("OpenAI funding 2025", num=10)

# Parse date string to days ago (handles "2 days ago" + "Jan 15, 2024")
days = parse_result_age(result['date'])

# Temporal weighting for attribution signals
strength, weight, label = parse_age_to_strength(result['date'])
# → ('strong', 1.0, '3 days ago')  or  ('weak', 0.3, 'Mar 2022')
```

Under the hood: POST to `https://google.serper.dev/search` with `X-API-KEY` header.

### Perplexity Sonar (OpenAI-compatible)

```python
headers = {
    'Authorization': f'Bearer {os.getenv("PERPLEXITY_API_KEY")}',
    'Content-Type': 'application/json',
}
payload = {
    'model': 'sonar',
    'messages': [{'role': 'user', 'content': query}],
    'temperature': 0.1,
    'max_tokens': 600,
    'search_recency_filter': 'year',
}
resp = requests.post('https://api.perplexity.ai/chat/completions',
                      headers=headers, json=payload, timeout=15)
data = resp.json()
content = data['choices'][0]['message']['content']
citations = data.get('citations', [])
```

---

## 6. Environment Variables

### Backend (Railway)

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `SERPER_API_KEY` | Serper.dev Google Search API |
| `PERPLEXITY_API_KEY` | Perplexity Sonar (optional — graceful no-op if absent) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon API key |
| `DASHBOARD_SECRET` | Bearer token for API auth (empty = no auth for local dev) |
| `CRON_SECRET` | Secret for cron endpoint authentication |
| `FRONTEND_URL` | Vercel dashboard URL (for CORS) |

### Frontend (Vercel)

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_URL` | Railway API URL (defaults to `http://localhost:8000`) |

---

## 7. Python Dependencies

```
python-dotenv>=1.0.0
pydantic>=2.0.0
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
supabase>=2.3.0
anthropic>=0.40.0
feedparser>=6.0.10
beautifulsoup4>=4.12.0
lxml>=5.1.0
dnspython>=2.4.0
requests>=2.31.0
```

---

## 8. Patterns for Reuse

### Adding a New API Router

1. Create `api/routers/my_router.py` with `router = APIRouter(prefix="/api/my-prefix")`
2. Register in `api/main.py`: `app.include_router(my_router.router, dependencies=[Depends(verify_token)])`

### Adding New Database Tables

1. Create `migrations/00N_description.sql`
2. If views depend on modified tables: `DROP VIEW IF EXISTS ... CASCADE` + recreate all dependent views
3. Always end with `NOTIFY pgrst, 'reload schema'`
4. Add methods to `app/core/database.py`

### Adding New Dashboard Pages

1. Create `dashboard/app/my-page/page.tsx`
2. Add nav link in `dashboard/app/layout.tsx`
3. Add types + fetch functions in `dashboard/lib/api.ts`
4. Use existing components from `dashboard/components/`

### Adding New Background Jobs

1. Create function in relevant module (e.g., `app/my_module/processor.py`)
2. Add entry point function: `def run_my_job(dry_run=False) -> dict`
3. Integrate into `api/routers/pipeline.py` `_run_pipeline_background()`:
   ```python
   try:
       from app.my_module.processor import run_my_job
       run_my_job()
   except Exception as e:
       print(f"⚠️ My job failed: {e}")
   ```

### Creating Backfill Scripts

Follow the pattern in `scripts/backfill_*.py`:
```python
import os, sys, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(override=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    # ... do work ...

if __name__ == '__main__':
    main()
```

---

## 9. Key Gotchas

1. **Supabase JSONB fields** may come back as strings (not parsed objects) depending on the SDK version and query method. Always handle both: `Array.isArray(x) ? x : JSON.parse(x)`.

2. **View CASCADE**: Dropping a view with CASCADE drops ALL dependent views. You must recreate them all in the migration.

3. **Railway auto-deploy lag**: If a cron job fires right after a push, it may run the old code. Sometimes needs manual redeploy from Railway dashboard.

4. **`claude-3-5-haiku-latest` is deprecated**. Use `claude-3-haiku-20240307` (or check what models are available on your API key).

5. **`.lstrip()` in Python strips characters, not substrings**: `"https://example.com".lstrip("https://")` strips all matching characters. Use `re.sub(r'^https?://', '', url)` instead.

6. **Perplexity/Haiku may append prose after JSON**. Always regex-extract the JSON portion before parsing.

7. **Serper.dev date strings** come in two formats: relative ("2 days ago") and absolute ("Jan 15, 2024"). Use `parse_result_age()` from `app/core/search` to normalize both to days.

8. **Cookie-based auth in Next.js**: `document.cookie` on client, `cookies()` from `next/headers` on server. The `apiFetch()` function handles both.

9. **Global `whitespace-nowrap` on table cells** in `dashboard/components/ui/table.tsx` — add `whitespace-normal` class to cells that need text wrapping.
