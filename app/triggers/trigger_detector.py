"""
Trigger Detection — monitors Tier 1+2 companies for inflection-point events.

Signals detected:
  - hiring_surge:      >=3 eng/infra roles posted
  - leadership_hire:   Head of Infra/Platform/Cloud, CTO, VP Eng new hire
  - product_launch:    Major launch/GA announcement in press or company blog
  - partnership:       Cloud-adjacent vendor partnership announcement
  - press_feature:     >=3 mentions in major tech press in last 14 days

Runs daily after recalculate_all_priorities(), only on Tier 1+2 companies.
Cost: ~5 Serper calls/company × ~50 Tier 1+2 companies = ~250 Serper calls/day.
"""

import os
import re
import requests
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DetectedTrigger:
    trigger_type: str           # hiring_surge | leadership_hire | product_launch | partnership | press_feature
    trigger_label: str          # Human-readable description
    detected_date: datetime     # When detected
    source_url: Optional[str]   # Evidence URL (None for aggregate triggers like hiring_surge)
    signal_strength: str        # "strong" | "moderate" | "weak"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEOUT = 6
HIRING_SURGE_THRESHOLD = 3

# Leadership titles that indicate a senior infrastructure hiring decision
_LEADERSHIP_TITLES = [
    'cto', 'chief technology officer',
    'vp engineering', 'vp of engineering', 'vice president engineering',
    'vp infrastructure', 'vp of infrastructure',
    'head of infrastructure', 'head of platform', 'head of cloud',
    'head of engineering', 'head of devops', 'head of sre',
]

# CTO-level titles get "strong", others get "moderate"
_CTO_TITLES = {'cto', 'chief technology officer'}

# Major tech press domains for press_feature detection
_AUTHORITY_DOMAINS = {
    'techcrunch.com', 'theverge.com', 'wired.com', 'venturebeat.com',
    'bloomberg.com', 'reuters.com', 'forbes.com', 'cnbc.com',
    'arstechnica.com', 'thenewstack.io', 'infoworld.com',
    'siliconangle.com', 'zdnet.com',
}

# Cloud-adjacent vendors for partnership detection
_CLOUD_ADJACENT_VENDORS = [
    'Snowflake', 'Databricks', 'Datadog', 'HashiCorp', 'Terraform',
    'Confluent', 'MongoDB', 'Elastic', 'Redis', 'CockroachDB',
    'Stripe', 'Twilio', 'Cloudflare', 'Fastly', 'Akamai',
    'PagerDuty', 'Splunk', 'New Relic', 'Grafana',
    'Docker', 'Kubernetes', 'Pulumi',
    'Vercel', 'Netlify', 'Supabase', 'PlanetScale', 'Neon',
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from app.core.search import serper_search, parse_result_age


def _confirm_hire_with_haiku(company_name: str, title: str, snippet: str) -> Optional[bool]:
    """
    Use Claude Haiku to confirm a search result is an actual hire, not a job posting.
    Returns True (confirmed hire), False (job posting), or None (couldn't determine / fail-open).
    """
    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None  # fail-open: no API key means we accept the result

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model='claude-3-5-haiku-latest',
            max_tokens=50,
            messages=[{
                'role': 'user',
                'content': (
                    f'Is this a news article about {company_name} actually hiring/appointing '
                    f'a new executive, or is it a job posting/recruitment ad?\n\n'
                    f'Title: {title}\nSnippet: {snippet}\n\n'
                    f'Reply with ONLY "hire" or "posting".'
                ),
            }],
        )
        answer = resp.content[0].text.strip().lower()
        return 'hire' in answer
    except Exception:
        return None  # fail-open


# ---------------------------------------------------------------------------
# Individual detectors (each returns Optional[DetectedTrigger])
# ---------------------------------------------------------------------------

def _detect_hiring_surge(company_name: str, website: str) -> Optional[DetectedTrigger]:
    """
    Detect >=3 eng/infra roles posted (hiring surge signal).
    Reuses the attribution engine's job board discovery via count_engineering_roles().
    """
    try:
        from app.attribution.attribution_engine import AttributionEngine
        engine = AttributionEngine()
        count, urls = engine.count_engineering_roles(company_name, website)
        if count >= HIRING_SURGE_THRESHOLD:
            return DetectedTrigger(
                trigger_type='hiring_surge',
                trigger_label=f'{count} infrastructure/engineering roles posted',
                detected_date=datetime.now(timezone.utc),
                source_url=urls[0] if urls else None,
                signal_strength='strong',
            )
    except Exception as e:
        print(f"      ⚠️  Hiring surge detection failed for {company_name}: {e}")
    return None


def _detect_leadership_hire(company_name: str) -> Optional[DetectedTrigger]:
    """
    Detect CTO / VP Eng / Head of Infra new hire announcement.
    Uses Brave search + optional Claude Haiku confirmation to filter job postings.
    """
    title_terms = (
        'CTO OR "VP Engineering" OR "Head of Infrastructure" OR '
        '"Head of Platform" OR "VP of Engineering" OR "Head of Engineering"'
    )
    action_terms = 'hired OR joins OR appointed OR names OR announces'
    query = f'"{company_name}" ({title_terms}) ({action_terms})'

    results = serper_search(query, num=5)
    for r in results:
        age_days = parse_result_age(r.get('date', ''))
        if age_days > 60:
            continue  # only care about recent hires

        title = r.get('title', '').lower()
        snippet = r.get('snippet', '').lower()
        combined = f'{title} {snippet}'

        # Check if any leadership title appears
        matched_title = None
        for lt in _LEADERSHIP_TITLES:
            if lt in combined:
                matched_title = lt
                break

        if not matched_title:
            continue

        # Haiku confirmation to filter out job postings vs actual hires (fail-open)
        is_confirmed = _confirm_hire_with_haiku(
            company_name, r.get('title', ''), r.get('snippet', '')
        )
        if is_confirmed is False:
            continue  # Haiku says it's a job posting, not an actual hire

        strength = 'strong' if matched_title in _CTO_TITLES else 'moderate'

        return DetectedTrigger(
            trigger_type='leadership_hire',
            trigger_label=f'New {matched_title.title()} announcement',
            detected_date=datetime.now(timezone.utc),
            source_url=r.get('url'),
            signal_strength=strength,
        )
    return None


def _detect_product_launch(company_name: str, domain: str) -> Optional[DetectedTrigger]:
    """Detect major product launch or GA announcement in last 30 days."""
    clean_domain = re.sub(r'^https?://', '', domain).rstrip('/')
    query = (
        f'"{company_name}" '
        f'(launch OR "generally available" OR "GA" OR "announces" OR "now available") '
        f'site:{clean_domain} OR site:techcrunch.com OR site:venturebeat.com OR site:theverge.com'
    )

    results = serper_search(query, num=5)
    for r in results:
        age_days = parse_result_age(r.get('date', ''))
        if age_days > 30:
            continue

        title = r.get('title', '')
        # Basic validation: title should mention the company
        if company_name.lower().split()[0] not in title.lower():
            continue

        return DetectedTrigger(
            trigger_type='product_launch',
            trigger_label=f'Product launch: {title[:80]}',
            detected_date=datetime.now(timezone.utc),
            source_url=r.get('url'),
            signal_strength='moderate',
        )
    return None


def _detect_partnership(company_name: str) -> Optional[DetectedTrigger]:
    """Detect partnership with cloud-adjacent vendor in last 60 days."""
    # Batch vendor names (5 per query) to limit Brave calls
    for i in range(0, len(_CLOUD_ADJACENT_VENDORS), 5):
        batch = _CLOUD_ADJACENT_VENDORS[i:i + 5]
        vendor_terms = ' OR '.join(f'"{v}"' for v in batch)
        query = f'"{company_name}" ({vendor_terms}) (partnership OR integration OR announces)'

        results = serper_search(query, num=5)
        for r in results:
            age_days = parse_result_age(r.get('date', ''))
            if age_days > 60:
                continue

            title = r.get('title', '')
            # Basic validation
            if company_name.lower().split()[0] not in title.lower():
                continue

            return DetectedTrigger(
                trigger_type='partnership',
                trigger_label=f'Partnership: {title[:80]}',
                detected_date=datetime.now(timezone.utc),
                source_url=r.get('url'),
                signal_strength='moderate',
            )
    return None


def _detect_press_feature(company_name: str) -> Optional[DetectedTrigger]:
    """Detect >=3 mentions in major tech press in last 14 days."""
    query = f'"{company_name}"'
    results = serper_search(query, num=20)

    recent_authority_urls = []
    for r in results:
        age_days = parse_result_age(r.get('date', ''))
        if age_days > 14:
            continue

        url = r.get('url', '')
        parsed_domain = urlparse(url).netloc.lstrip('www.')

        if parsed_domain in _AUTHORITY_DOMAINS:
            recent_authority_urls.append(url)

    if len(recent_authority_urls) >= 3:
        return DetectedTrigger(
            trigger_type='press_feature',
            trigger_label=f'{len(recent_authority_urls)} major press mentions in 14 days',
            detected_date=datetime.now(timezone.utc),
            source_url=recent_authority_urls[0],
            signal_strength='moderate',
        )
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def detect_triggers(
    company_name: str,
    domain: str,
    existing_triggers: list[dict],
) -> list[DetectedTrigger]:
    """
    Run all trigger detectors for a company.
    Deduplicates against existing_triggers by (trigger_type, source_url).
    Returns only NEW triggers.
    """
    existing_keys = {
        (t['trigger_type'], t.get('source_url'))
        for t in existing_triggers
    }

    new_triggers: list[DetectedTrigger] = []
    detectors = [
        lambda: _detect_hiring_surge(company_name, domain),
        lambda: _detect_leadership_hire(company_name),
        lambda: _detect_product_launch(company_name, domain),
        lambda: _detect_partnership(company_name),
        lambda: _detect_press_feature(company_name),
    ]

    for detector in detectors:
        try:
            result = detector()
            if result:
                key = (result.trigger_type, result.source_url)
                if key not in existing_keys:
                    new_triggers.append(result)
                    existing_keys.add(key)
        except Exception as e:
            print(f"      ⚠️  Trigger detector failed: {e}")

    return new_triggers


# ---------------------------------------------------------------------------
# Tier upgrade logic
# ---------------------------------------------------------------------------

def apply_trigger_upgrades(
    current_tier: int,
    cloud_propensity: str,
    all_triggers: list,
) -> int:
    """
    Evaluate whether triggers should upgrade the company's engagement tier.
    Triggers can only upgrade (lower tier number), never downgrade.

    - >=2 strong triggers + High propensity → Tier 1
    - 1 strong trigger + High propensity → minimum Tier 2
    """
    strong_triggers = [
        t for t in all_triggers
        if (t.signal_strength if hasattr(t, 'signal_strength') else t.get('signal_strength')) == 'strong'
    ]

    # Multiple strong triggers on High propensity → Tier 1
    if len(strong_triggers) >= 2 and cloud_propensity == 'High':
        return min(current_tier, 1)

    # Single strong trigger on High propensity → minimum Tier 2
    if strong_triggers and cloud_propensity == 'High':
        return min(current_tier, 2)

    return current_tier


# ---------------------------------------------------------------------------
# Main entry point (called by cron pipeline)
# ---------------------------------------------------------------------------

def run_trigger_detection(dry_run: bool = False) -> dict:
    """
    Run trigger detection for all Tier 1+2 companies.
    Called after recalculate_all_priorities() in the daily cron.

    Returns summary dict: {"companies_scanned": N, "triggers_found": N, "upgrades": N}
    """
    from supabase import create_client
    from app.priority import TIER_LABELS

    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

    # Fetch Tier 1+2 companies
    res = sb.table('latest_attributions').select(
        'id, canonical_name, website, engagement_tier, cloud_propensity'
    ).in_('engagement_tier', [1, 2]).execute()

    companies = res.data or []
    if not companies:
        print("  ℹ️  No Tier 1/2 companies found for trigger detection")
        return {"companies_scanned": 0, "triggers_found": 0, "upgrades": 0}

    print(f"\n  🔍 Trigger detection: scanning {len(companies)} Tier 1/2 companies")

    total_triggers = 0
    total_upgrades = 0

    for company in companies:
        company_id = company['id']
        name = company['canonical_name']
        website = company.get('website', '')
        current_tier = company.get('engagement_tier', 3)
        propensity = company.get('cloud_propensity', 'Low')

        # Fetch existing triggers for dedup
        existing = sb.table('company_triggers').select(
            'trigger_type, source_url'
        ).eq('company_id', company_id).execute()
        existing_triggers = existing.data or []

        # Run detection
        print(f"    Scanning {name}...")
        new_triggers = detect_triggers(name, website, existing_triggers)

        if new_triggers:
            print(f"    ✅ {name}: {len(new_triggers)} new trigger(s)")
            total_triggers += len(new_triggers)

            if not dry_run:
                # Store triggers (upsert with dedup index)
                for t in new_triggers:
                    sb.table('company_triggers').upsert({
                        'company_id': company_id,
                        'trigger_type': t.trigger_type,
                        'trigger_label': t.trigger_label,
                        'signal_strength': t.signal_strength,
                        'source_url': t.source_url,
                        'detected_date': t.detected_date.isoformat(),
                    }, on_conflict='company_id,trigger_type,source_url').execute()

                # Fetch ALL triggers for tier upgrade evaluation
                all_trigger_rows = sb.table('company_triggers').select('*').eq(
                    'company_id', company_id
                ).execute()
                all_triggers_data = all_trigger_rows.data or []

                # Check for tier upgrade
                new_tier = apply_trigger_upgrades(current_tier, propensity, all_triggers_data)
                if new_tier < current_tier:
                    strong_count = len([
                        t for t in all_triggers_data if t.get('signal_strength') == 'strong'
                    ])
                    print(f"    ⬆️  {name}: upgraded Tier {current_tier} → Tier {new_tier}")
                    total_upgrades += 1

                    sb.table('attribution_snapshots').update({
                        'engagement_tier': new_tier,
                        'engagement_tier_label': TIER_LABELS.get(new_tier, f'Tier {new_tier}'),
                        'engagement_tier_rationale': (
                            f'Upgraded by trigger detection: '
                            f'{strong_count} strong trigger(s) + {propensity} propensity'
                        ),
                    }).eq('startup_id', company_id).execute()

                # Update active trigger count on snapshot
                trigger_count = len(all_triggers_data)
                sb.table('attribution_snapshots').update({
                    'active_trigger_count': trigger_count,
                }).eq('startup_id', company_id).execute()

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n  📊 {prefix}Trigger detection complete:")
    print(f"     Companies scanned: {len(companies)}")
    print(f"     New triggers found: {total_triggers}")
    print(f"     Tier upgrades: {total_upgrades}")

    return {
        "companies_scanned": len(companies),
        "triggers_found": total_triggers,
        "upgrades": total_upgrades,
    }
