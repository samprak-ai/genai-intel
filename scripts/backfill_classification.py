"""
Backfill vertical classification for existing companies.

Fetches all companies from attribution_snapshots where vertical IS NULL,
classifies each using the LLM classifier, and patches the snapshot.

Run synchronously (not as a background task) to avoid Railway cron overlap:
    python3 scripts/backfill_classification.py

Optionally limit to N companies:
    python3 scripts/backfill_classification.py --limit 10

Dry-run mode (classify but don't write to DB):
    python3 scripts/backfill_classification.py --dry-run
"""

import os
import sys
import time
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from supabase import create_client
from app.classification.classifier import classify_company


def backfill(limit: int = 0, dry_run: bool = False):
    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

    # Fetch latest snapshot per startup where vertical is null.
    # Join with startups table to get company name, description, investors.
    query = (
        sb.table('attribution_snapshots')
        .select('id, startup_id, snapshot_date')
        .is_('vertical', 'null')
        .order('snapshot_date', desc=True)
    )
    if limit:
        query = query.limit(limit)

    snapshots = query.execute().data
    print(f"Found {len(snapshots)} snapshots with null vertical")

    if not snapshots:
        return

    # Deduplicate: keep only the latest snapshot per startup_id
    seen_startups = set()
    unique_snapshots = []
    for snap in snapshots:
        sid = snap['startup_id']
        if sid not in seen_startups:
            seen_startups.add(sid)
            unique_snapshots.append(snap)

    print(f"Unique startups to classify: {len(unique_snapshots)}")

    # Fetch startup details for all unique startup_ids
    startup_ids = [s['startup_id'] for s in unique_snapshots]
    startups_data = {}
    # Batch fetch in chunks of 50
    for i in range(0, len(startup_ids), 50):
        chunk = startup_ids[i:i+50]
        res = sb.table('startups').select(
            'id, canonical_name, website, description, industry, lead_investors, founder_background'
        ).in_('id', chunk).execute()
        for row in res.data:
            startups_data[row['id']] = row

    classified = 0
    failed = 0

    for snap in unique_snapshots:
        startup = startups_data.get(snap['startup_id'])
        if not startup:
            print(f"  ⚠️  Startup {snap['startup_id']} not found in startups table")
            failed += 1
            continue

        name = startup.get('canonical_name', '')
        domain = startup.get('website', '')
        desc = startup.get('description', '') or startup.get('industry', '') or ''
        investors = startup.get('lead_investors', None)
        founder_bg = startup.get('founder_background', None)

        # Investors may be stored as JSON array string or list
        if isinstance(investors, str):
            try:
                import json
                investors = json.loads(investors)
            except Exception:
                investors = [investors] if investors else None
        elif not isinstance(investors, list):
            investors = None

        # Same for founder_background
        if isinstance(founder_bg, list):
            founder_bg = ", ".join(founder_bg) if founder_bg else None
        elif not isinstance(founder_bg, str):
            founder_bg = None

        try:
            result = classify_company(
                company_name=name,
                domain=domain,
                description=desc,
                investors=investors,
                founder_background=founder_bg,
            )

            prop = result.cloud_propensity or 'None'
            conf = result.classification_confidence or 'None'
            sv = result.sub_vertical or 'None'
            v = result.vertical or 'None'

            print(f"  {name:30s} | {v:35s} | {sv:40s} | prop={prop:6s} | conf={conf}")

            if not dry_run and result.vertical:
                # Patch ALL snapshots for this startup (not just the latest)
                sb.table('attribution_snapshots').update({
                    'vertical': result.vertical,
                    'sub_vertical': result.sub_vertical,
                    'cloud_propensity': result.cloud_propensity,
                    'classification_confidence': result.classification_confidence,
                    'classification_source': result.classification_source,
                }).eq('startup_id', snap['startup_id']).execute()

            classified += 1

        except Exception as e:
            print(f"  ❌ {name}: {e}")
            failed += 1

        # Rate limit: avoid hammering Anthropic API
        time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"Backfill complete: {classified} classified, {failed} failed")
    if dry_run:
        print("(dry-run — no DB writes)")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Backfill vertical classification')
    parser.add_argument('--limit', type=int, default=0, help='Max companies to classify (0=all)')
    parser.add_argument('--dry-run', action='store_true', help='Classify but don\'t write to DB')
    args = parser.parse_args()

    backfill(limit=args.limit, dry_run=args.dry_run)
