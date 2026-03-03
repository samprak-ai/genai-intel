# GenAI-Intel

**Live AI intelligence platform — cloud and AI provider attribution for VC-funded startups**

🔗 **[cloud-intel.vercel.app](https://cloud-intel.vercel.app)** · [github.com/[YOUR-USERNAME]/genai-intel](https://github.com/[YOUR-USERNAME]/genai-intel)

**Status: Live · Daily automated pipeline · 110+ startups tracked**

---

## What It Is

GenAI-Intel is a live intelligence platform that tracks VC-funded startups and determines which cloud and AI infrastructure providers they're building on — with weighted confidence scores and entrenchment ratings (WEAK / MODERATE / STRONG) for every attribution.

It answers a question that anyone in AI sales, VC, or competitive intelligence faces every week: *"Which cloud and AI providers are the most promising new startups actually building on?"*

Getting that answer manually means reading press releases, scraping job boards, and piecing together signals from a dozen sources — for every company, every week. GenAI-Intel automates it entirely and surfaces it in a queryable, filterable dashboard.

---

## The Product

**Dashboard** — Top-level metrics: companies tracked, dominant cloud and AI providers, market share breakdown, and a recent funding events feed with attribution inline.

**Companies** — Searchable, filterable database of all tracked startups. Filter by cloud provider, AI provider, or date range. Each row shows provider attribution, confidence score, and entrenchment signal.

**Add Company** — Manual enrichment flow. Submit a company with website, evidence URLs (Tier 1 signals), investor background, and founder context. Attribution runs automatically on submission.

**Pipeline Runs** — Full operational monitoring. Every run shows discovered count, attributed count, errors, duration, and timestamp with drill-down logs. Pipeline triggers daily at 06:00 UTC.

---

## Architecture

```
  Google News RSS
        │
        ▼
  LLM Extraction          (company name, funding round, amount, investors)
        │
        ▼
  Domain Resolution       (map company → verified domain, deduplicate)
        │
        ▼
  Attribution Engine      (4-tier signal system — see below)
        │
        ▼
  Supabase               (attribution_snapshots: confidence, raw_score, entrenchment)
        │
        ▼
  Next.js Dashboard       (cloud-intel.vercel.app)
```

**Stack:** FastAPI (Railway) · Supabase · Next.js (Vercel) · Claude API · Perplexity Sonar · Brave Search API

---

## Attribution Engine

The core intelligence layer. Uses a 4-tier signal system with weighted confidence scoring and entrenchment classification per attribution.

### Signal Tiers

| Tier | Type | Weight | Sources |
|---|---|---|---|
| Tier 1 | Deterministic | 1.0 | DNS CNAME, subprocessors/DPA pages, partnership announcements (temporally weighted), case studies, marketplace listings |
| Tier 2 | Strong inference | 0.6 | Job postings, integration/partners pages, technical docs, trust/security/privacy pages, HTTP headers |
| Tier 3 | Supporting | 0.3 | IP/ASN ranges, tech blog posts, homepage keyword mentions |
| Tier 4a | Perplexity Sonar | mapped | Live web search fallback when confidence < 60% after Tiers 1–3 |
| Tier 4b | Claude Haiku | mapped | Reads pre-fetched homepage + article text as final fallback |

### Confidence Formula

```
scaled_confidence = min(raw_score / 2.0, 1.0)
```

Examples: IP/ASN only (0.3) → 15% · 1 job posting (0.6) → 30% · 1 partnership (1.0) → 50% · 2 strong signals (2.0) → 100%

### Entrenchment Classification

WEAK · MODERATE · STRONG — based on raw signal score thresholds. Displayed as chips on every company row.

---

## Key Technical Details

**Brave API batching** — Partnership signals use 3 batched OR-queries instead of 21 per-provider calls, reducing Brave API usage from ~40 to ~7 calls per company.

**Perplexity Sonar (Tier 4a)** — Fires when confidence < 60% after Tiers 1–3. Uses `sonar` model with `search_recency_filter='year'`. Citations returned by the API are stored as evidence URLs on signals. Weight mapping: ≥80 → 1.0 STRONG, 50–79 → 0.6 MEDIUM.

**Job posting scanning** — Probes Lever, Greenhouse, Ashby, Workable, and Rippling ATS slug-based URLs. Handles Ashby's SPA rendering by parsing `window.__appData` from inline scripts. Engineering roles prioritized; scans first 8 URLs after sorting.

**Website content scanning** — Scans privacy policy, DPA, security, trust, and compliance pages. Ownership-language regex (`processed by`, `stored on`, `servers located`) triggers STRONG weight signals.

**Investor job boards** — Getro-based VC boards (a16z, Sequoia, 8VC, etc.) scanned via XML sitemaps with Brave fallback.

---

## Tech Stack

| Layer | Tools |
|---|---|
| API / Backend | FastAPI (Python) |
| AI — extraction & fallback | Claude API (Anthropic) — Claude Haiku |
| AI — live web search | Perplexity Sonar |
| Search | Brave Search API |
| Database | Supabase (PostgreSQL) |
| Frontend | Next.js |
| API deployment | Railway (auto-deploys on push to main) |
| Dashboard deployment | Vercel |
| Cron | cron-job.org → POST /api/pipeline/cron |
| Built with | Claude Code |

---

## Sample Output

```json
{
  "company": "Ease Health",
  "domain": "easehealth.com",
  "funding_round": "Series A",
  "amount_usd": 41000000,
  "announced_date": "2026-03-03",
  "cloud_provider": "AWS",
  "cloud_confidence": 0.30,
  "cloud_raw_score": 0.60,
  "cloud_entrenchment": "WEAK",
  "ai_provider": "OpenAI",
  "ai_confidence": 0.67,
  "ai_raw_score": 1.34,
  "ai_entrenchment": "MODERATE"
}
```

---

## Why I Built This

I spent years at AWS working on competitive intelligence for the Startups org — understanding which cloud providers the most promising new startups were choosing, and why. That research was almost entirely manual.

This project automates the part of that work that should never have required a human in the first place.

It's also a personal proof-of-concept. I'm not a software engineer by background. I built this entirely using Claude Code and modern AI tooling — to test how far product thinking and AI tools can take someone without a traditional engineering foundation.

The answer, running live daily: pretty far.

---

## Setup

```bash
# Clone the repo
git clone https://github.com/[YOUR-USERNAME]/genai-intel.git
cd genai-intel

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Required: ANTHROPIC_API_KEY, BRAVE_SEARCH_API_KEY, SUPABASE_URL, SUPABASE_KEY
# Optional: PERPLEXITY_API_KEY (Tier 4a fallback), CRON_SECRET, DASHBOARD_SECRET

# Run the pipeline
python pipeline.py
```

> A `.env.example` is included. Never commit your `.env` — it's in `.gitignore`.
> Perplexity Sonar degrades gracefully if `PERPLEXITY_API_KEY` is absent.

---

## What's Next

- Expanding provider coverage to more AI infrastructure tools
- Improving confidence scoring to reduce false positives on generic company names
- Export and alerting features for tracked companies
- Broader funding announcement source coverage

---

## Contact

Built by [Sam Prakash](https://linkedin.com/in/samprakash) — GTM leader and AI product builder.

Open to conversations about AI products, competitive intelligence tooling, and roles at the intersection of product and AI.
