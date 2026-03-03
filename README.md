# GenAI-Intel

**Automated VC Intelligence — Who's funding whom, and what tech are they building on?**

GenAI-Intel is a fully automated weekly intelligence pipeline running in production. It discovers venture capital funding announcements and determines which cloud and AI infrastructure providers each funded startup uses — turning a manual, hours-long research process into a structured, queryable dataset refreshed every week without human intervention.

> Built end-to-end by a non-engineer using Claude Code, Python, and modern AI infrastructure.

**Status: Live in production · Weekly pipeline · 30–40 startups processed per cycle**

---

## The Problem It Solves

If you work in AI sales, VC, or competitive intelligence, you've faced this question: *"Which cloud and AI providers are the most promising new startups actually building on?"*

Getting that answer today means manually reading press releases, scraping job boards, and piecing together signals from a dozen sources — for every single company, every single week. It doesn't scale.

GenAI-Intel automates that entirely.

---

## What It Does

Every week, the pipeline automatically:

1. **Discovers** 30–40 new VC funding announcements from RSS feeds and web sources
2. **Resolves** each startup to a verified domain and company profile
3. **Attributes** cloud and AI provider usage (AWS, Azure, GCP, Anthropic, OpenAI, etc.) using a tiered confidence scoring system
4. **Stores** structured results in a database ready for querying and analysis

The output is a continuously growing dataset of funded startups mapped to their technical infrastructure choices — immediately useful for competitive intelligence, sales targeting, and market trend analysis.

---

## How It Works

The pipeline runs in four stages:

```
  DISCOVERY  ──►  RESOLUTION  ──►  ATTRIBUTION  ──►  STORAGE
  RSS feeds       Domain mapping    Signal tiering     Supabase DB
  Web sources     Company profile   Confidence score   Query-ready
  News crawl      Deduplication     Provider logic     Structured
```

### Stage 1 — Discovery
Monitors a curated set of RSS feeds and web sources for weekly VC funding announcements. Filters for relevant funding rounds and extracts core company metadata: name, round size, investors, and sector.

### Stage 2 — Resolution
Maps each company to a verified domain. Handles edge cases like redirects, brand name vs. legal entity mismatches, and duplicate announcements across sources.

### Stage 3 — Attribution
The intelligence core. Uses a tiered signal system to determine which cloud and AI providers a startup is built on, with a confidence score attached to every attribution:

| Signal Tier | Where the signal comes from | Confidence |
|---|---|---|
| Tier 1 | Job postings, official tech stack pages | High |
| Tier 2 | Website scanning (meta tags, script sources, DNS) | Medium |
| Tier 3 | Press releases, news mentions, partnership announcements | Medium |
| Tier 4 | Inference from investor / accelerator profile | Low |

Providers tracked include AWS, Azure, GCP, Anthropic, OpenAI, Cohere, Mistral, and other major AI infrastructure tools.

### Stage 4 — Storage
Every result is written to Supabase with a structured schema: company profile, funding details, attributed providers, confidence scores, and signal sources. Designed for easy downstream querying and analysis.

---

## Sample Output

Each processed startup produces a structured record like this:

```json
{
  "company": "ExampleAI",
  "domain": "exampleai.com",
  "funding_round": "Series A",
  "amount_usd": 12000000,
  "announced_date": "2026-02-17",
  "investors": ["Sequoia", "a16z"],
  "providers_attributed": [
    {
      "provider": "AWS",
      "confidence": 0.87,
      "signal_tier": 1,
      "signal_source": "job_posting"
    },
    {
      "provider": "Anthropic",
      "confidence": 0.72,
      "signal_tier": 2,
      "signal_source": "website_scan"
    }
  ]
}
```

---

## Tech Stack

| Layer | Tools |
|---|---|
| Core language | Python |
| AI / LLM | Claude API (Anthropic) |
| Orchestration | LangGraph |
| Database | Supabase (PostgreSQL) |
| Deployment | Railway |
| Built with | Claude Code |

---

## Why I Built This

I spent years at AWS working on competitive intelligence for the Startups org — understanding which cloud providers the most promising new startups were choosing, and why. That research was almost entirely manual.

This project automates the part of that work that should never have required a human in the first place.

It's also a personal proof-of-concept. I'm not a software engineer by background. I built this entirely using Claude Code and modern AI tooling — to test how far product thinking and AI tools can take someone without a traditional engineering foundation.

The answer, running in production weekly: pretty far.

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
- Improving marketplace detection through direct website scanning
- Refining confidence scoring to reduce false positives in partnership detection
- Broadening funding announcement sources beyond tier-1 tech press

---

## Contact

Built by [Sam Prakash](https://linkedin.com/in/samprakash) — GTM leader and AI product builder.

Open to conversations about AI products, competitive intelligence tooling, and roles at the intersection of product and AI.
