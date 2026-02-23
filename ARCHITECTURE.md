# GenAI-Intel V2 - Enhanced Architecture

## Core Philosophy (Preserved from V1)

```
Deterministic > LLM
Evidence > Assumptions
Signals > Hunches
Structured > Unstructured
```

**New Addition:** Transparency > Black Box
→ Every inference must show its evidence trail

---

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    WEEKLY PIPELINE                          │
└─────────────────────────────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
    ┌──────────────┐ ┌─────────────┐ ┌─────────────┐
    │   DISCOVER   │ │   RESOLVE   │ │  ATTRIBUTE  │
    │   Funding    │ │   Domain    │ │   Cloud/AI  │
    └──────────────┘ └─────────────┘ └─────────────┘
            │               │               │
            └───────────────┼───────────────┘
                            ▼
                    ┌──────────────┐
                    │   SUPABASE   │
                    │   DATABASE   │
                    └──────────────┘
```

---

## Phase 1: Funding Discovery

### Sources (Priority Order)

1. **Crunchbase API** (if available)
   - Most structured
   - Highest accuracy
   - API key required

2. **TechCrunch RSS Feed**
   - Free
   - Good coverage
   - Requires parsing

3. **Google News API**
   - Backup source
   - Broader coverage
   - May have noise

### Process Flow

```python
def discover_funding_events():
    """
    1. Fetch from all sources
    2. Deduplicate by (company_name, amount, date)
    3. LLM extraction with Pydantic validation
    4. Store raw article + extracted JSON
    5. Return validated funding events
    """
```

### LLM Extraction Schema

```python
from pydantic import BaseModel, Field
from datetime import date
from typing import Optional

class FundingEvent(BaseModel):
    company_name: str = Field(..., description="Official company name")
    funding_amount_usd: int = Field(..., description="Amount in millions USD")
    funding_round: str = Field(..., description="Seed, Series A, B, C, etc.")
    funding_date: Optional[date] = Field(None, description="When funding closed")
    announcement_date: date = Field(..., description="When announced")
    lead_investors: list[str] = Field(default_factory=list)
    website: Optional[str] = Field(None, description="Company website if mentioned")
    industry: Optional[str] = Field(None, description="Industry/sector")
```

**Key Improvement:** Pydantic enforces structure, LLM fills it deterministically

---

## Phase 2: Domain Resolution

### V1 Approach (Your Current)
- LLM extracts website from article
- Rejects social media

### V2 Enhancement

```python
def resolve_official_domain(company_name: str, article_text: str) -> str:
    """
    Priority cascade:
    1. Extract from article (deterministic regex)
    2. DNS lookup for company-name.com variants
    3. LLM web search as last resort
    4. Validation: Must be root domain, not subdomain
    """
    
    # 1. Deterministic extraction
    domain = extract_domain_from_text(article_text)
    if domain and validate_domain(domain):
        return domain
    
    # 2. DNS-based guessing
    candidates = [
        f"{company_name.lower().replace(' ', '')}.com",
        f"{company_name.lower().replace(' ', '-')}.com",
        f"{company_name.lower().replace(' ', '')}.ai"
    ]
    
    for candidate in candidates:
        if dns_lookup_succeeds(candidate):
            return candidate
    
    # 3. LLM web search (last resort)
    return llm_search_official_website(company_name)
```

**Validation Rules:**
- ✅ Root domains only (company.com)
- ❌ No linkedin.com/company/X
- ❌ No crunchbase.com/organization/X
- ❌ No twitter.com/X
- ❌ No subdomains unless official (docs.company.com is OK if company.com redirects)

---

## Phase 3: Cloud & AI Attribution

### The Core Innovation: Evidence-Based Scoring

```
┌──────────────────────────────────────────────────────┐
│            SIGNAL GATHERING (Deterministic)          │
└──────────────────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌─────────────┐ ┌─────────────┐
│ Partnership  │ │    DNS/     │ │   Website   │
│   Override   │ │   CNAME     │ │   Parsing   │
│  (STRONG)    │ │  (STRONG)   │ │  (WEAK)     │
└──────────────┘ └─────────────┘ └─────────────┘
        │               │               │
        └───────────────┼───────────────┘
                        ▼
                ┌──────────────┐
                │   WEIGHTED   │
                │   SCORING    │
                └──────────────┘
                        │
                        ▼
                ┌──────────────┐
                │  CONFIDENCE  │
                │  THRESHOLD   │
                └──────────────┘
```

### Signal Sources (Ranked by Reliability)

#### Tier 1: Partnership Overrides (Weight: 1.0)

```python
PARTNERSHIP_OVERRIDES = {
    "Anthropic": {"cloud": "GCP", "ai": "Anthropic"},
    "OpenAI": {"cloud": "Azure", "ai": "OpenAI"},
    "Cohere": {"cloud": "AWS", "ai": "Cohere"},
    # ... maintained list
}
```

**Source:** Official press releases, verified partnerships

#### Tier 2: Infrastructure Signals (Weight: 1.0)

```python
def check_dns_cname(domain: str) -> dict:
    """
    CNAME checks reveal actual hosting:
    - *.amazonaws.com → AWS
    - *.cloudfront.net → AWS
    - *.googleusercontent.com → GCP
    - *.ghs.googlehosted.com → GCP
    - *.azurewebsites.net → Azure
    - *.cloudflaressl.com → Cloudflare (not cloud provider)
    """
```

**HTTP Headers:**
```python
def check_http_headers(domain: str) -> dict:
    """
    Server headers reveal hosting:
    - X-Amz-Cf-Id → AWS CloudFront
    - X-Cloud-Trace-Context → GCP
    - X-Azure-Ref → Azure
    """
```

#### Tier 3: Website Content (Weight: 0.6)

```python
def parse_trust_pages(domain: str) -> dict:
    """
    Parse /security, /trust, /privacy for mentions:
    - "data stored in AWS"
    - "hosted on Google Cloud"
    - "infrastructure powered by Azure"
    """
```

#### Tier 4: Job Postings (Weight: 0.6)

```python
def analyze_job_postings(company_name: str) -> dict:
    """
    Search LinkedIn/Indeed for:
    - "AWS experience required"
    - "GCP certification preferred"
    - "Azure DevOps knowledge"
    """
```

#### Tier 5: LLM Analysis (Weight: 0.3 - Tie-breaker only)

```python
def llm_infer_from_context(company_name: str, signals: dict) -> dict:
    """
    Use Claude to analyze:
    - Company description
    - Industry patterns
    - Existing weak signals
    
    Only if score < 1.0 after deterministic signals
    """
```

### Scoring Algorithm

```python
def calculate_attribution_score(signals: list[Signal]) -> Attribution:
    """
    1. Start with score = 0 for each provider
    2. For each signal:
       score[provider] += signal.strength * signal.weight
    3. Winner = max(score)
    4. Confidence = score[winner] / sum(all_scores)
    5. Entrenchment = map_score_to_level(score[winner])
    """
    
    scores = defaultdict(float)
    
    for signal in signals:
        weight = STRENGTH_WEIGHTS[signal.strength]
        scores[signal.provider] += weight
    
    winner = max(scores, key=scores.get)
    total = sum(scores.values())
    
    return Attribution(
        provider=winner,
        confidence=scores[winner] / total if total > 0 else 0,
        entrenchment=calculate_entrenchment(scores[winner]),
        evidence_count=len(signals),
        signals=signals  # Store for transparency!
    )
```

### Entrenchment Levels (Your Original - Preserved)

```python
def calculate_entrenchment(score: float) -> str:
    if score >= 2.0:
        return "STRONG"     # Multiple strong signals
    elif score >= 1.0:
        return "MODERATE"   # One strong or multiple medium
    elif score >= 0.3:
        return "WEAK"       # Only weak signals
    else:
        return "UNKNOWN"    # No evidence
```

---

## Enhanced Database Schema

### startups (Core Entity)

```sql
CREATE TABLE startups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name TEXT UNIQUE NOT NULL,
    website TEXT UNIQUE,
    industry TEXT,
    description TEXT,
    
    -- Metadata
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    
    -- Indexes
    CONSTRAINT valid_website CHECK (website ~* '^https?://[a-z0-9-]+\.[a-z]+')
);

CREATE INDEX idx_startups_name ON startups(canonical_name);
CREATE INDEX idx_startups_website ON startups(website);
```

### funding_events (Your Original - Enhanced)

```sql
CREATE TABLE funding_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    startup_id UUID REFERENCES startups(id) ON DELETE CASCADE,
    
    -- Funding details
    funding_amount_usd BIGINT NOT NULL,
    funding_round TEXT NOT NULL,
    funding_date DATE,
    announcement_date DATE NOT NULL,
    
    -- Lead investors
    lead_investors TEXT[],
    
    -- Source tracking
    source_name TEXT NOT NULL,  -- 'techcrunch', 'crunchbase', etc.
    source_url TEXT NOT NULL,
    raw_article_text TEXT,
    extracted_json JSONB NOT NULL,
    
    -- Metadata
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT valid_amount CHECK (funding_amount_usd > 0)
);

CREATE INDEX idx_funding_startup ON funding_events(startup_id);
CREATE INDEX idx_funding_date ON funding_events(announcement_date);
```

### attribution_signals (NEW - Evidence Storage)

```sql
CREATE TABLE attribution_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    startup_id UUID REFERENCES startups(id) ON DELETE CASCADE,
    
    -- Signal details
    provider_type TEXT NOT NULL,  -- 'cloud' or 'ai'
    provider_name TEXT NOT NULL,  -- 'AWS', 'OpenAI', etc.
    signal_source TEXT NOT NULL,  -- 'dns', 'partnership', 'jobs', etc.
    signal_strength TEXT NOT NULL, -- 'STRONG', 'MEDIUM', 'WEAK'
    
    -- Evidence
    evidence_text TEXT,
    evidence_url TEXT,
    confidence_weight FLOAT NOT NULL,
    
    -- Metadata
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT valid_provider_type CHECK (provider_type IN ('cloud', 'ai')),
    CONSTRAINT valid_strength CHECK (signal_strength IN ('STRONG', 'MEDIUM', 'WEAK'))
);

CREATE INDEX idx_signals_startup ON attribution_signals(startup_id);
CREATE INDEX idx_signals_provider ON attribution_signals(provider_name);
```

### attribution_snapshots (Your Original - Enhanced)

```sql
CREATE TABLE attribution_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    startup_id UUID REFERENCES startups(id) ON DELETE CASCADE,
    
    -- Cloud attribution
    pcp_provider TEXT,              -- Primary Cloud Provider
    pcp_confidence FLOAT,
    pcp_entrenchment TEXT,
    pcp_evidence_count INT,
    
    -- AI attribution  
    ai_provider TEXT,
    ai_confidence FLOAT,
    ai_entrenchment TEXT,
    ai_evidence_count INT,
    
    -- Snapshot metadata
    snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Prevent duplicate snapshots per day
    UNIQUE(startup_id, snapshot_date)
);

CREATE INDEX idx_snapshots_startup ON attribution_snapshots(startup_id);
CREATE INDEX idx_snapshots_date ON attribution_snapshots(snapshot_date);
```

### weekly_runs (Automation Tracking)

```sql
CREATE TABLE weekly_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Run details
    run_date DATE NOT NULL DEFAULT CURRENT_DATE,
    startups_discovered INT DEFAULT 0,
    startups_attributed INT DEFAULT 0,
    errors_count INT DEFAULT 0,
    
    -- Performance
    execution_time_seconds INT,
    
    -- Status
    status TEXT DEFAULT 'running',  -- running, completed, failed
    error_log JSONB,
    
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    
    UNIQUE(run_date)
);
```

---

## Key Improvements Over V1

### 1. Evidence Transparency ✨
**V1:** Just stores final score
**V2:** Stores every signal with source, strength, and evidence text

**Why:** Debugging, trust, auditing

### 2. Historical Tracking ✨
**V1:** Single snapshot, overwrites
**V2:** Daily snapshots, track changes over time

**Why:** Detect migrations (AWS→GCP), validate stability

### 3. AI Provider Detection ✨
**V1:** Mentioned but not implemented
**V2:** Full parallel attribution for AI providers

**Why:** Your use case needs both cloud AND AI insights

### 4. Deterministic-First, Always ✨
**V1:** LLM as "optional tie-breaker"
**V2:** Clear cascade: Partnership → DNS → Headers → Content → Jobs → LLM

**Why:** Maximum transparency, minimum hallucination

### 5. Better Domain Resolution ✨
**V1:** LLM extracts from article
**V2:** Regex → DNS guessing → LLM (cascade)

**Why:** 80% of websites follow company-name.com pattern

---

## Next Steps

I can now build:

1. **Complete Python implementation** with this architecture
2. **Supabase migration scripts** from your current schema
3. **GitHub Actions automation** for weekly runs
4. **CLI tools** for manual runs and debugging
5. **Analytics queries** for insights

Would you like me to:
- **A)** Build the complete Python codebase with this architecture?
- **B)** Start with migration from your V1 to V2?
- **C)** Focus on a specific phase (Discovery, Resolution, or Attribution)?

Your deterministic-first philosophy is excellent - I'm just making it more transparent and adding the AI provider detection you mentioned. 🚀
