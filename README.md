# GenAI-Intel

**Live AI intelligence platform — cloud and AI provider attribution for VC-funded startups**

🔗 **[cloud-intel.vercel.app](https://cloud-intel.vercel.app)** · [github.com/[YOUR-USERNAME]/genai-intel](https://github.com/[YOUR-USERNAME]/genai-intel)

**Status: Live · Daily automated pipeline · 110+ startups tracked**

---

## What It Is

GenAI-Intel is a live intelligence platform that tracks VC-funded startups and determines which cloud and AI infrastructure providers they're building on — with confidence scores and entrenchment signals for every attribution.

It answers a question that anyone in AI sales, VC, or competitive intelligence faces every week: *"Which cloud and AI providers are the most promising new startups actually building on?"*

Getting that answer manually means reading press releases, scraping job boards, and piecing together signals from a dozen sources — for every company, every week. GenAI-Intel automates it entirely, and surfaces it in a queryable, filterable dashboard.

---

## The Product

Four views, all live:

**Dashboard** — Top-level metrics: companies tracked, dominant cloud and AI providers, market share breakdown via pie charts, and a recent funding events feed with attribution inline.

**Companies** — Searchable, filterable database of all tracked startups. Columns include cloud provider, confidence score, entrenchment signal (WEAK / STRONG), AI provider, funding amount, and announcement date. Filter by cloud, AI provider, or date range.

**Add Company** — Manual enrichment flow. Submit a company with website, evidence URLs (Tier 1 signals), investor background, and founder context. Attribution runs automatically on submission.

**Pipeline Runs** — Full operational monitoring. Every run shows discovered count, attributed count, errors, duration, and timestamp. Drill into logs for any run. Pipeline triggers daily at 06:00 UTC.

---

## How It Works

```
  DISCOVERY  ──►  RESOLUTION  ──►  ATTRIBUTION  ──►  STORAGE  ──►  DASHBOARD
  RSS feeds       Domain mapping    Signal tiering     Supabase DB    Vercel
  Web sources     Company profile   Confidence score   Query-ready    Live UI
  News crawl      Deduplication     Entrenchment       Structured     Daily refresh
```

### Attribution Engine
The core intelligence layer uses a tiered signal system with a confidence score and entrenchment rating attached to every attribution:

| Signal Tier | Source | Confidence |
|---|---|---|
| Tier 1 | Job postings, official tech stack pages, evidence URLs | High |
| Tier 2 | Website scanning (meta tags, script sources, DNS) | Medium |
| Tier 3 | Press releases, news mentions, partnership announcements | Medium |
| Tier 4 | Inference from investor / accelerator profile | Low |

Providers tracked: AWS, Azure, GCP, Anthropic, OpenAI, Hugging Face, Replicate, and other major AI infrastructure tools.

---

## Tech Stack

| Layer | Tools |
|---|---|
| Core language | Python |
| AI / LLM | Claude API (Anthropic) |
| Orchestration | LangGraph |
| Database | Supabase (PostgreSQL) |
| Frontend | Next.js |
| Deployment | Railway (pipeline) · Vercel (dashboard) |
| Built with | Claude Code |

---

## Sample Output

```json
{
  "company": "Ease Health",
  "domain": "techfundingnews.com",
  "funding_round": "Series A",
  "amount_usd": 41000000,
  "announced_date": "2026-03-03",
  "cloud_provider": "AWS",
  "cloud_confidence": 0.30,
  "cloud_entrenchment": "WEAK",
  "ai_provider": "OpenAI",
  "ai_confidence": 0.67
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
# Add your keys: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_KEY

# Run the pipeline
python main.py
```

> A `.env.example` file is included. Never commit your `.env` — it's in `.gitignore`.

---

## What's Next

- Expanding provider attribution to cover more AI infrastructure tools
- Improving confidence scoring to reduce false positives
- Broader funding announcement source coverage
- Export and alerting features for tracked companies

---

## Contact

Built by [Sam Prakash](https://linkedin.com/in/samprakash) — GTM leader and AI product builder.

Open to conversations about AI products, competitive intelligence tooling, and roles at the intersection of product and AI.
