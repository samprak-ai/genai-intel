# Feature Spec: Phase 2 — Engagement Priority, Trigger Detection & Outreach Intelligence

## Overview

Phase 2 transforms GenAI-Intel from an intelligence database into an actionable engagement platform. It adds three connected layers on top of the Phase 1 foundation:

1. **Engagement Priority** — A Tier 1 / 2 / 3 classification derived from existing data (funding recency, propensity, entrenchment). No new data sources needed. Build this first.
2. **Trigger Detection** — A signal monitoring pipeline that watches tracked companies for inflection-point events (hiring surges, leadership changes, product launches, partnerships). Runs against the existing tracked company set only.
3. **Outreach Intelligence** — LLM-generated engagement angle per company, grounded in all available signals. Surfaces in a "Ready to Engage" dashboard view.

---

## Design Principles (carry forward from Phase 1)

- **Tags over scores.** Priority is Tier 1 / 2 / 3, not a number. Timing is Hot / Warm / Watch, not a decimal. Avoids false precision.
- **Funding is the entry gate.** Trigger detection runs only against tracked (funded) companies. Do not expand the discovery pipeline to track unfunded companies.
- **Signals upgrade tiers, they don't create new tracked companies.** A hiring surge on a company not already in the system does not add that company. It only matters if the company is already tracked.
- **Public data only.** All signal sources are publicly accessible. No CRM integration, no proprietary data feeds.
- **Stateless classifier pattern.** Each new module should be independently testable with just company metadata as input.

---

## Part 1: Engagement Priority

### What it is

A three-tier priority classification derived entirely from data already in `attribution_snapshots`. No new API calls. No new data sources. Computable today against all 110+ existing records.

### Tier definitions

| Tier | Label | Criteria | Action |
|---|---|---|---|
| Tier 1 | **Engage Now** | Raised ≤90 days ago AND propensity = High AND entrenchment = WEAK or Unknown | Active outreach window |
| Tier 2 | **Watch** | Raised ≤180 days ago AND propensity = High or Medium AND any entrenchment | Monitor; engage on trigger |
| Tier 3 | **Track** | Everything else in the pipeline | Maintain; surface on change |

### Tier calculation logic

```python
# app/priority.py

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

TIER1_FUNDING_WINDOW_DAYS = 90
TIER2_FUNDING_WINDOW_DAYS = 180

@dataclass
class PriorityResult:
    tier: int                    # 1, 2, or 3
    tier_label: str              # "Engage Now", "Watch", "Track"
    tier_rationale: str          # One-line explanation for dashboard tooltip

def calculate_priority(
    funding_date: datetime | None,
    funding_amount_usd: int | None,
    cloud_propensity: str | None,       # "High" / "Medium" / "Low"
    cloud_entrenchment: str | None,     # "STRONG" / "MODERATE" / "WEAK" / None
) -> PriorityResult:

    if not funding_date:
        return PriorityResult(3, "Track", "No funding date available")

    now = datetime.now(timezone.utc)
    days_since_funding = (now - funding_date).days

    # Tier 1: active decision window
    if (
        days_since_funding <= TIER1_FUNDING_WINDOW_DAYS
        and cloud_propensity == "High"
        and cloud_entrenchment in ("WEAK", None, "Unknown")
    ):
        rationale = (
            f"Raised {days_since_funding}d ago · High propensity · "
            f"{'Weak' if cloud_entrenchment == 'WEAK' else 'Unknown'} cloud entrenchment — "
            f"infrastructure decision likely in progress"
        )
        return PriorityResult(1, "Engage Now", rationale)

    # Tier 2: watch window
    if (
        days_since_funding <= TIER2_FUNDING_WINDOW_DAYS
        and cloud_propensity in ("High", "Medium")
    ):
        rationale = (
            f"Raised {days_since_funding}d ago · {cloud_propensity} propensity · "
            f"Monitor for trigger events"
        )
        return PriorityResult(2, "Watch", rationale)

    # Tier 3: everything else
    rationale = (
        f"Raised {days_since_funding}d ago · {cloud_propensity or 'Unknown'} propensity"
    )
    return PriorityResult(3, "Track", rationale)
```

### Database changes

```sql
ALTER TABLE attribution_snapshots
  ADD COLUMN IF NOT EXISTS engagement_tier        INTEGER CHECK (engagement_tier IN (1, 2, 3)),
  ADD COLUMN IF NOT EXISTS engagement_tier_label  TEXT,
  ADD COLUMN IF NOT EXISTS engagement_tier_rationale TEXT,
  ADD COLUMN IF NOT EXISTS tier_last_calculated   TIMESTAMPTZ;
```

### Integration points

- **Pipeline:** Calculate priority at end of `_build_snapshot()`, after attribution and classification are complete. All inputs are available at that point.
- **Backfill:** Run a one-off script to calculate priority for all existing records where `engagement_tier IS NULL`.
- **Recalculation:** Priority must recalculate on every pipeline run for every tracked company — not just newly discovered ones — because `days_since_funding` changes daily and a Tier 1 company ages into Tier 2 after 90 days.

### Backfill + daily recalculation script

```python
# scripts/recalculate_priority.py
# Run this: (a) once after migration to backfill, (b) daily via cron

async def recalculate_all_priorities():
    companies = await supabase.fetch_all_for_priority_recalc()
    for company in companies:
        result = calculate_priority(
            funding_date=company.announced_date,
            funding_amount_usd=company.funding_amount,
            cloud_propensity=company.cloud_propensity,
            cloud_entrenchment=company.cloud_entrenchment,
        )
        await supabase.update_priority(company.id, result)
```

Add `recalculate_all_priorities()` to the end of the existing daily pipeline run so priority stays current.

---

## Part 2: Trigger Detection

### What it is

A signal monitoring pipeline that watches the existing tracked company set for inflection-point events. Runs daily, after the main discovery pipeline. Detects signals that indicate a company is at a decision point — and updates their engagement tier accordingly.

### Signal types

| Signal | Source | Detection method | Tier impact |
|---|---|---|---|
| Infrastructure/DevOps hiring surge | Job boards (Lever, Greenhouse, Ashby, Workable) | ≥3 eng/infra roles posted in last 30 days | Tier 2 → 1 if propensity High |
| First "Head of Infrastructure / Platform / Cloud" hire | Job boards + LinkedIn (public) | Title keyword match on new senior role | Tier upgrade + flag |
| CTO / VP Engineering leadership change | LinkedIn public profiles, press | New hire announcement in press or job posting | Tier upgrade + flag |
| Major product launch / GA announcement | Company blog, Product Hunt, tech press | Brave search: `"{company}" launch OR "generally available" OR "GA"` | Warm signal |
| Partnership with cloud-adjacent vendor | Press, company blog | Brave search: `"{company}" partnership OR integration` + vendor name filter | Warm signal |
| Featured in major tech press | News API / Brave | Recent high-authority publication mention | Warm signal |

### New module: `app/triggers/trigger_detector.py`

```python
@dataclass
class DetectedTrigger:
    trigger_type: str           # "hiring_surge" | "leadership_hire" | "product_launch" | "partnership" | "press_feature"
    trigger_label: str          # Human-readable: "3 infrastructure roles posted"
    detected_date: datetime
    source_url: str | None      # Evidence URL
    signal_strength: str        # "strong" | "moderate" | "weak"

async def detect_triggers(
    company_name: str,
    domain: str,
    existing_triggers: list[DetectedTrigger],   # previously detected, to avoid duplicates
) -> list[DetectedTrigger]:
    ...
```

### Detection implementation

**Hiring surge detection** — reuse and extend the existing `_check_job_postings()` logic from the attribution engine. Instead of just checking for cloud-relevant roles, count all engineering/infrastructure roles posted in the last 30 days. Threshold: ≥3 roles = surge signal.

**Leadership hire detection** — Brave search: `"{company_name}" (CTO OR "VP Engineering" OR "Head of Infrastructure" OR "Head of Platform") hired OR joins OR appointed`. Filter results to last 60 days. Parse with Claude Haiku to confirm it's a real hire, not a job posting.

**Product launch detection** — Brave search: `"{company_name}" (launch OR "generally available" OR "GA" OR "announces") site:{domain} OR site:techcrunch.com OR site:venturebeat.com`. Filter to last 30 days.

**Partnership detection** — Reuse the existing partnership signal logic from the attribution engine (`_brave_batches`). Extend to detect non-cloud partnerships (Snowflake, Databricks, Stripe, Twilio, etc.) as warm signals.

**Press feature detection** — Brave search: `"{company_name}"` filtered to last 14 days, authority domain filter. Count mentions. ≥3 in 14 days = warm signal.

### Deduplication

Before storing a detected trigger, check if the same `trigger_type` + `source_url` already exists for this company. If yes, skip. Triggers are immutable once stored — they represent a historical record of what happened and when.

### Database changes

```sql
CREATE TABLE IF NOT EXISTS company_triggers (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id        UUID REFERENCES attribution_snapshots(id),
    trigger_type      TEXT NOT NULL,
    trigger_label     TEXT NOT NULL,
    signal_strength   TEXT CHECK (signal_strength IN ('strong', 'moderate', 'weak')),
    source_url        TEXT,
    detected_date     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_company_triggers_company_id ON company_triggers(company_id);
CREATE INDEX idx_company_triggers_detected_date ON company_triggers(detected_date DESC);
```

### Tier upgrade logic

After trigger detection runs for a company, re-evaluate its engagement tier:

```python
def apply_trigger_upgrades(
    current_tier: int,
    cloud_propensity: str,
    new_triggers: list[DetectedTrigger],
) -> int:
    strong_triggers = [t for t in new_triggers if t.signal_strength == "strong"]

    # Strong trigger on High propensity company → minimum Tier 2
    if strong_triggers and cloud_propensity == "High":
        return min(current_tier, 2)

    # Multiple strong triggers → Tier 1 regardless of funding recency
    if len(strong_triggers) >= 2 and cloud_propensity == "High":
        return 1

    return current_tier
```

This means a Tier 3 company (raised >180 days ago) can be upgraded to Tier 2 if it fires a strong trigger. Two strong triggers on a High propensity company push it to Tier 1 regardless of funding age.

### Pipeline integration

Run trigger detection daily, after the main discovery pipeline, only against **Tier 1 and Tier 2 companies**. Do not run against Tier 3 — too expensive and too low signal.

```python
# pipeline.py — end of daily run

tier_1_2_companies = await supabase.fetch_companies_by_tier([1, 2])
for company in tier_1_2_companies:
    triggers = await detect_triggers(
        company_name=company.name,
        domain=company.domain,
        existing_triggers=await supabase.fetch_triggers(company.id),
    )
    if triggers:
        await supabase.store_triggers(company.id, triggers)
        new_tier = apply_trigger_upgrades(
            current_tier=company.engagement_tier,
            cloud_propensity=company.cloud_propensity,
            new_triggers=triggers,
        )
        if new_tier != company.engagement_tier:
            await supabase.update_tier(company.id, new_tier)
```

### Cost estimate

Trigger detection uses Brave Search (existing). Approximately 5-6 Brave calls per company per day.

At 50 Tier 1+2 companies: ~250-300 Brave calls/day. Well within existing API budget.

Claude Haiku for leadership hire confirmation: ~10 calls/day at $0.0002 each = negligible.

---

## Part 3: Outreach Intelligence

### What it is

An LLM-generated engagement angle per company, grounded in all available signals. Not a template — a specific reasoning chain that tells a GTM person why this company is worth reaching out to, what angle to take, and what to reference.

### New module: `app/intelligence/outreach_generator.py`

```python
@dataclass
class OutreachIntelligence:
    engagement_timing: str          # "Hot" | "Warm" | "Watch"
    recommended_angle: str          # 2-3 sentence engagement rationale
    key_signals: list[str]          # Bullet points driving the recommendation
    generated_at: datetime
    model_used: str                 # For audit trail

async def generate_outreach_intelligence(
    company_name: str,
    vertical: str,
    sub_vertical: str,
    cloud_propensity: str,
    cloud_provider: str | None,
    cloud_confidence: float | None,
    cloud_entrenchment: str | None,
    ai_provider: str | None,
    funding_amount: int | None,
    funding_round: str | None,
    funding_date: datetime | None,
    engagement_tier: int,
    recent_triggers: list[DetectedTrigger],
) -> OutreachIntelligence:
    ...
```

### Engagement timing derivation

Derived from tier + recent trigger activity — not a separate LLM call:

```python
def derive_engagement_timing(
    engagement_tier: int,
    recent_triggers: list[DetectedTrigger],
    days_since_last_trigger: int | None,
) -> str:
    strong_recent = [
        t for t in recent_triggers
        if t.signal_strength == "strong"
        and (datetime.now(timezone.utc) - t.detected_date).days <= 14
    ]
    if engagement_tier == 1 and strong_recent:
        return "Hot"
    if engagement_tier in (1, 2):
        return "Warm"
    return "Watch"
```

### LLM prompt for recommended angle

Use **Claude Haiku**. Generate only for Tier 1 and Tier 2 companies. Regenerate when a new trigger fires or tier changes.

```python
OUTREACH_PROMPT = """
You are generating a concise engagement intelligence briefing for a cloud provider GTM team.

Company: {company_name}
Vertical: {vertical} → {sub_vertical}
Cloud Propensity: {cloud_propensity}
Current Cloud Stack: {cloud_provider} ({cloud_confidence}% confidence, {cloud_entrenchment} entrenchment)
Current AI Stack: {ai_provider}
Funding: {funding_round} of {funding_amount} raised {days_since_funding} days ago
Engagement Tier: {tier_label}

Recent signals:
{trigger_list}

Generate a concise outreach intelligence briefing with:
1. A recommended engagement angle (2-3 sentences, specific to this company's situation)
2. 3-4 key signals that support this recommendation (short bullet points)

The audience is a cloud provider sales or partnerships person. Be direct, specific, and grounded
in the signals. Do not use generic sales language. Reference the actual data points.

Respond with JSON only:
{{
  "recommended_angle": "<2-3 sentence engagement rationale>",
  "key_signals": ["<signal 1>", "<signal 2>", "<signal 3>"]
}}
"""
```

### Example output

For Ease Health (Series A, Health-data analytics, AWS 30% WEAK, OpenAI 67%, 3 infra roles posted):

```json
{
  "recommended_angle": "Ease Health is in the infrastructure decision window — raised their Series A 45 days ago and is actively hiring infrastructure engineers, suggesting they're architecting their data platform now. With HIPAA-eligible workloads and weak AWS entrenchment, the conversation should center on managed database options and compliance-ready infrastructure before the stack gets locked in. OpenAI dependency at 67% confidence suggests AI inference costs will grow fast — worth positioning Bedrock as a cost and compliance alternative.",
  "key_signals": [
    "Series A raised 45 days ago — active spend authorization window",
    "3 infrastructure engineering roles posted in last 30 days",
    "AWS attribution at 30% confidence (WEAK) — stack decision not finalized",
    "Health-data analytics sub-vertical: HIPAA compliance is a differentiated AWS angle"
  ]
}
```

### Database changes

```sql
ALTER TABLE attribution_snapshots
  ADD COLUMN IF NOT EXISTS engagement_timing      TEXT CHECK (engagement_timing IN ('Hot', 'Warm', 'Watch')),
  ADD COLUMN IF NOT EXISTS recommended_angle      TEXT,
  ADD COLUMN IF NOT EXISTS key_signals            JSONB,
  ADD COLUMN IF NOT EXISTS intelligence_generated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS intelligence_model     TEXT;
```

### Regeneration trigger

Regenerate outreach intelligence when:
- A new trigger is detected for the company
- Engagement tier changes
- Attribution changes (cloud or AI provider updated)
- Intelligence is older than 7 days for Tier 1 companies, 14 days for Tier 2

Do not regenerate for Tier 3 companies.

---

## Dashboard Changes

### Navigation — existing tabs must not change

The existing navigation tabs (Dashboard, Companies, Add Company, Pipeline Runs) must remain exactly as they are. Do not modify any existing tab routes, layouts, components, or behaviour. All Phase 2 dashboard work is additive only.

Add a single new top-level navigation tab:

```
Dashboard  |  Companies  |  Add Company  |  Pipeline Runs  |  Ready to Engage  ← new
```

**Implementation:** Create a new route at `dashboard/app/ready-to-engage/page.tsx`. Add a single nav link entry pointing to `/ready-to-engage` in the existing nav component. Touch nothing else in the nav.

---

### New tab: "Ready to Engage"

Route: `/ready-to-engage`
File: `dashboard/app/ready-to-engage/page.tsx`

A self-contained view. Does not share state or layout with the Companies table. Default filter: Tier 1 only. Toggle to include Tier 2.

**Columns:**
- Company name + domain
- Vertical / Sub-vertical
- Cloud Propensity chip (High/Medium/Low)
- Engagement Timing chip (Hot/Warm/Watch)
- Active Triggers (count badge + expandable list)
- Recommended Angle (expandable 2-3 sentence text)
- Funding (round + amount + days ago)
- Cloud attribution + confidence

**Sort:** Default by Engagement Timing (Hot first), then by funding recency.

### Updates to Companies table

Add new columns (toggleable, off by default to avoid clutter):
- Engagement Tier (1 / 2 / 3 chip)
- Engagement Timing (Hot / Warm / Watch chip)
- Active Triggers (count badge)

### Updates to company detail view

Each company record page gains a new "Engagement Intelligence" section:
- Engagement tier + rationale
- Engagement timing
- Recommended angle
- Active triggers (list with timestamps, signal strength, source URLs)
- Trigger history (all triggers ever detected, sorted by date)

### Updates to main Dashboard

Two new summary cards:
```
TIER 1 COMPANIES          ACTIVE TRIGGERS
[N] Engage Now            [N] last 7 days
[N] added this week       [N] strong signals
```

---

## Build Order

### Phase 2a — Priority (no new data sources, build first)
1. Create `app/priority.py` with `calculate_priority()` and `PriorityResult`
2. Run SQL migration for priority columns on `attribution_snapshots`
3. Run backfill script on all existing records
4. Integrate `calculate_priority()` into end of `_build_snapshot()`
5. Add daily `recalculate_all_priorities()` call to pipeline cron
6. Add Engagement Tier chip to Companies table in dashboard
7. Add Tier filter dropdown to Companies table

### Phase 2b — Trigger Detection
8. Create `app/triggers/trigger_detector.py` with all signal detectors
9. Run SQL migration for `company_triggers` table
10. Integrate trigger detection into daily pipeline (Tier 1+2 only)
11. Add trigger upgrade logic to tier recalculation
12. Add Active Triggers column and expandable list to Companies table

### Phase 2c — Outreach Intelligence
13. Create `app/intelligence/outreach_generator.py`
14. Run SQL migration for intelligence columns on `attribution_snapshots`
15. Generate outreach intelligence for all existing Tier 1+2 companies (backfill)
16. Integrate regeneration triggers into pipeline
17. Build "Ready to Engage" dashboard view
18. Add Engagement Intelligence section to company detail view
19. Add Tier 1 and Active Triggers summary cards to main Dashboard

---

## Cost Estimate (Phase 2 incremental)

| Component | Volume | Cost |
|---|---|---|
| Priority recalculation | All companies daily | $0 (no API calls) |
| Trigger detection (Brave) | ~300 calls/day (50 Tier 1+2 companies × 6) | ~$0.03/day at $0.01/call |
| Leadership hire confirmation (Haiku) | ~10 calls/day | ~$0.002/day |
| Outreach intelligence generation (Haiku) | ~15 regenerations/day | ~$0.003/day |
| **Total Phase 2 incremental** | | **~$0.035/day · ~$13/year** |

---

## Notes for Claude Code

- Build Phase 2a completely before starting 2b. Priority tiers are required by trigger detection (which only runs on Tier 1+2) and by outreach generation.
- `calculate_priority()` must be called on **every** company on **every** pipeline run — not just new discoveries. Aging from Tier 1 to Tier 2 after 90 days is a time-based event, not a data event.
- The `company_triggers` table is append-only. Never delete or update trigger records — they are a historical log. Deduplication happens at insert time by checking `trigger_type` + `source_url` + `company_id`.
- Outreach intelligence is regenerated, not versioned. The current `recommended_angle` in `attribution_snapshots` is always the latest. If you want history, add a separate `outreach_intelligence_history` table later.
- The "Ready to Engage" view is the primary product surface for any external demo. Prioritize its polish over the other dashboard updates.
- Do not run trigger detection against Tier 3 companies. The API cost is justified only for companies already in the active window.
- All trigger source URLs should be stored — they are the evidence layer that makes the recommended angle credible and verifiable.
