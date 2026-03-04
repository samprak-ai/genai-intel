"""
Outreach Intelligence — generates LLM-driven engagement angles for Tier 1+2 companies.

Uses Claude Haiku to produce company-specific engagement rationale grounded in all
available signals (attribution, funding, triggers). Engagement timing (Hot/Warm/Watch)
is derived deterministically from tier + recent trigger activity.

Runs daily after trigger detection, only on Tier 1+2 companies.
Cost: ~$0.003/day for ~15 Haiku calls.
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class OutreachIntelligence:
    engagement_timing: str          # "Hot" | "Warm" | "Watch"
    recommended_angle: Optional[str]  # 2-3 sentence engagement rationale (None if LLM fails)
    key_signals: list[str]          # 3-4 bullet points
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    model_used: str = "claude-3-5-haiku-latest"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = "claude-3-5-haiku-latest"

OUTREACH_PROMPT = """You are generating a concise engagement intelligence briefing for a cloud provider GTM team.

Company: {company_name}
Vertical: {vertical} → {sub_vertical}
Cloud Propensity: {cloud_propensity}
Current Cloud Stack: {cloud_provider} ({cloud_confidence}% confidence, {cloud_entrenchment} entrenchment)
Current AI Stack: {ai_provider}
Funding: {funding_round} of ${funding_amount}M raised {days_since_funding} days ago
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
}}"""

TIER_LABELS = {1: "Engage Now", 2: "Watch", 3: "Track"}


# ---------------------------------------------------------------------------
# Engagement timing (deterministic — no LLM call)
# ---------------------------------------------------------------------------

def derive_engagement_timing(
    engagement_tier: int,
    recent_triggers: list[dict],
) -> str:
    """
    Derive Hot/Warm/Watch from tier + recent trigger activity.
    - Tier 1 + strong trigger within 14 days → "Hot"
    - Tier 1 or 2 → "Warm"
    - Everything else → "Watch"
    """
    now = datetime.now(timezone.utc)

    strong_recent = []
    for t in recent_triggers:
        if t.get('signal_strength') != 'strong':
            continue
        detected = t.get('detected_date')
        if not detected:
            continue
        if isinstance(detected, str):
            try:
                detected = datetime.fromisoformat(detected.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                continue
        if hasattr(detected, 'tzinfo') and detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)
        if (now - detected).days <= 14:
            strong_recent.append(t)

    if engagement_tier == 1 and strong_recent:
        return "Hot"
    if engagement_tier in (1, 2):
        return "Warm"
    return "Watch"


# ---------------------------------------------------------------------------
# LLM-generated recommended angle
# ---------------------------------------------------------------------------

def generate_outreach_intelligence(
    company_name: str,
    vertical: Optional[str],
    sub_vertical: Optional[str],
    cloud_propensity: Optional[str],
    cloud_provider: Optional[str],
    cloud_confidence: Optional[float],
    cloud_entrenchment: Optional[str],
    ai_provider: Optional[str],
    funding_amount: Optional[float],
    funding_round: Optional[str],
    funding_date: Optional[str],
    engagement_tier: int,
    recent_triggers: list[dict],
) -> OutreachIntelligence:
    """
    Generate outreach intelligence for a company using Claude Haiku.

    Returns OutreachIntelligence with recommended_angle=None if the LLM call fails.
    """
    # Step 1: Derive engagement timing (deterministic)
    timing = derive_engagement_timing(engagement_tier, recent_triggers)

    # Step 2: Calculate days since funding
    days_since_funding = "unknown"
    if funding_date:
        try:
            if isinstance(funding_date, str):
                fd = datetime.fromisoformat(funding_date.replace('Z', '+00:00'))
            else:
                fd = funding_date
            if hasattr(fd, 'tzinfo') and fd.tzinfo is None:
                fd = fd.replace(tzinfo=timezone.utc)
            days_since_funding = str((datetime.now(timezone.utc) - fd).days)
        except (ValueError, TypeError):
            pass

    # Step 3: Format trigger list
    if recent_triggers:
        trigger_lines = []
        for t in recent_triggers:
            strength = t.get('signal_strength', 'unknown')
            label = t.get('trigger_label', t.get('trigger_type', 'unknown'))
            trigger_lines.append(f"- [{strength}] {label}")
        trigger_list = "\n".join(trigger_lines)
    else:
        trigger_list = "- No recent triggers detected"

    # Step 4: Build prompt
    prompt = OUTREACH_PROMPT.format(
        company_name=company_name,
        vertical=vertical or "Unknown",
        sub_vertical=sub_vertical or "Unknown",
        cloud_propensity=cloud_propensity or "Unknown",
        cloud_provider=cloud_provider or "Unknown",
        cloud_confidence=round((cloud_confidence or 0) * 100),
        cloud_entrenchment=cloud_entrenchment or "Unknown",
        ai_provider=ai_provider or "Unknown",
        funding_amount=funding_amount or "Unknown",
        funding_round=funding_round or "Unknown",
        days_since_funding=days_since_funding,
        tier_label=TIER_LABELS.get(engagement_tier, f"Tier {engagement_tier}"),
        trigger_list=trigger_list,
    )

    # Step 5: Call Claude Haiku
    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        print(f"    ⚠️  No ANTHROPIC_API_KEY — skipping LLM generation for {company_name}")
        return OutreachIntelligence(
            engagement_timing=timing,
            recommended_angle=None,
            key_signals=[],
        )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = resp.content[0].text.strip()

        # Parse JSON (Haiku may append prose after the JSON object)
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            parsed = json.loads(json_match.group())
            return OutreachIntelligence(
                engagement_timing=timing,
                recommended_angle=parsed.get('recommended_angle'),
                key_signals=parsed.get('key_signals', []),
            )
        else:
            print(f"    ⚠️  Could not parse JSON from Haiku response for {company_name}")
            return OutreachIntelligence(
                engagement_timing=timing,
                recommended_angle=None,
                key_signals=[],
            )

    except Exception as e:
        print(f"    ⚠️  Haiku call failed for {company_name}: {e}")
        return OutreachIntelligence(
            engagement_timing=timing,
            recommended_angle=None,
            key_signals=[],
        )


# ---------------------------------------------------------------------------
# Main entry point — run for all Tier 1+2 companies
# ---------------------------------------------------------------------------

def run_outreach_generation(dry_run: bool = False) -> dict:
    """
    Generate/refresh outreach intelligence for all Tier 1+2 companies.
    Called after run_trigger_detection() in the daily cron.

    Regeneration rules:
    - Always generate if engagement_timing is NULL (never generated)
    - Skip if intelligence is < 7 days old (Tier 1) or < 14 days old (Tier 2)

    Returns summary dict: {"companies_processed": N, "generated": N, "skipped": N}
    """
    from supabase import create_client

    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

    # Fetch Tier 1+2 companies with all needed fields
    res = sb.table('latest_attributions').select(
        'id, canonical_name, website, engagement_tier, '
        'cloud_propensity, cloud_primary_provider, cloud_confidence, cloud_entrenchment, '
        'ai_primary_provider, vertical, sub_vertical, '
        'engagement_timing, intelligence_generated_at'
    ).in_('engagement_tier', [1, 2]).execute()

    companies = res.data or []
    if not companies:
        print("  ℹ️  No Tier 1/2 companies found for outreach generation")
        return {"companies_processed": 0, "generated": 0, "skipped": 0}

    print(f"\n  🧠 Outreach intelligence: processing {len(companies)} Tier 1/2 companies")

    now = datetime.now(timezone.utc)
    total_generated = 0
    total_skipped = 0

    for company in companies:
        company_id = company['id']
        name = company['canonical_name']
        tier = company.get('engagement_tier', 3)

        # Check if regeneration is needed
        generated_at_str = company.get('intelligence_generated_at')
        if generated_at_str:
            try:
                generated_at = datetime.fromisoformat(
                    generated_at_str.replace('Z', '+00:00')
                )
                if generated_at.tzinfo is None:
                    generated_at = generated_at.replace(tzinfo=timezone.utc)
                age_days = (now - generated_at).days
                max_age = 7 if tier == 1 else 14
                if age_days < max_age:
                    total_skipped += 1
                    continue
            except (ValueError, TypeError):
                pass  # regenerate if we can't parse the timestamp

        # Fetch recent funding for this company
        funding_res = sb.table('funding_events').select(
            'funding_amount_usd, funding_round, announcement_date'
        ).eq('startup_id', company_id).order(
            'announcement_date', desc=True
        ).limit(1).execute()
        funding = funding_res.data[0] if funding_res.data else {}

        # Fetch triggers for this company
        triggers_res = sb.table('company_triggers').select('*').eq(
            'company_id', company_id
        ).order('detected_date', desc=True).execute()
        triggers = triggers_res.data or []

        print(f"    Generating intelligence for {name}...")

        if dry_run:
            timing = derive_engagement_timing(tier, triggers)
            print(f"    [DRY RUN] Would generate: timing={timing}")
            total_generated += 1
            continue

        # Generate intelligence
        intelligence = generate_outreach_intelligence(
            company_name=name,
            vertical=company.get('vertical'),
            sub_vertical=company.get('sub_vertical'),
            cloud_propensity=company.get('cloud_propensity'),
            cloud_provider=company.get('cloud_primary_provider'),
            cloud_confidence=company.get('cloud_confidence'),
            cloud_entrenchment=company.get('cloud_entrenchment'),
            ai_provider=company.get('ai_primary_provider'),
            funding_amount=funding.get('funding_amount_usd'),
            funding_round=funding.get('funding_round'),
            funding_date=funding.get('announcement_date'),
            engagement_tier=tier,
            recent_triggers=triggers,
        )

        # Update attribution_snapshots
        update_data = {
            'engagement_timing': intelligence.engagement_timing,
            'recommended_angle': intelligence.recommended_angle,
            'key_signals': json.dumps(intelligence.key_signals) if intelligence.key_signals else None,
            'intelligence_generated_at': intelligence.generated_at.isoformat(),
            'intelligence_model': intelligence.model_used,
        }
        sb.table('attribution_snapshots').update(
            update_data
        ).eq('startup_id', company_id).execute()

        total_generated += 1

        if intelligence.recommended_angle:
            print(f"    ✅ {name}: {intelligence.engagement_timing} — angle generated")
        else:
            print(f"    ⚠️  {name}: {intelligence.engagement_timing} — angle generation failed")

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n  📊 {prefix}Outreach intelligence complete:")
    print(f"     Companies processed: {len(companies)}")
    print(f"     Intelligence generated: {total_generated}")
    print(f"     Skipped (still fresh): {total_skipped}")

    return {
        "companies_processed": len(companies),
        "generated": total_generated,
        "skipped": total_skipped,
    }
