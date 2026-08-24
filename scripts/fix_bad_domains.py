"""
Fix bad domain records in the database.

Deletes the 12 startups with wrong domains (badge.com, nimble.com, etc.)
then re-resolves and re-attributes them using the improved domain resolver.

Usage:
    python3 scripts/fix_bad_domains.py          # dry run (preview only)
    python3 scripts/fix_bad_domains.py --apply  # actually delete + re-resolve
"""

import os
import sys
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

# ── Startups known to have wrong domains ─────────────────────────────────────
BAD_DOMAINS = [
    'badge.com',
    'nimble.com',
    'jump.com',
    'pulse.com',
    'circuit.com',
    'ascent.com',
    'croissant.com',
    'basis.com',
    'turbine.com',
    'unknown.com',
    'letterai.com',
    'pepper.com',
]

# Context pulled from the original funding events for better AI resolution.
# Format: company_name → (funding_round, amount_M, description, industry)
COMPANY_CONTEXT = {
    'Badge':      ('Seed',     17,  'Fintech startup, rewards and loyalty',     'Fintech'),
    'Nimble':     ('Series B', 47,  'Robotics company, warehouse automation',    'Robotics'),
    'Jump':       ('Series B', 80,  'AI/software startup',                      'AI'),
    'Pulse':      ('Series A', None,'AI/software startup',                      'AI'),
    'Circuit':    ('Series A', None,'AI/software startup',                      'AI'),
    'Ascent':     ('Series A', None,'AI/software startup',                      'AI'),
    'Croissant':  ('Seed',     None,'AI/software startup',                      'AI'),
    'Basis':      ('Series A', None,'AI/software startup',                      'AI'),
    'Turbine':    ('Series B', 25,  'Drug discovery AI, Hungary',               'Biotech/AI'),
    'Unknown':    (None,       None, None,                                       None),
    'Letter Ai':  ('Seed',     None,'AI writing/productivity startup',           'AI'),
    'Pepper':     ('Series B', None,'AI/software startup',                      'AI'),
}


def get_bad_startups(db):
    """Fetch all startups whose website is in the bad domains list."""
    result = db.client.table('startups').select('id, canonical_name, website').execute()
    bad = [s for s in result.data if s['website'] in BAD_DOMAINS]
    return bad


def delete_startup_completely(db, startup_id: str, startup_name: str):
    """
    Delete a startup and all its associated rows.
    Table FK order: attribution_snapshots → attribution_signals → funding_events → startups
    """
    # 1. Attribution snapshots
    db.client.table('attribution_snapshots').delete().eq('startup_id', startup_id).execute()
    print(f"    🗑  Deleted attribution_snapshots for {startup_name}")

    # 2. Attribution signals
    db.client.table('attribution_signals').delete().eq('startup_id', startup_id).execute()
    print(f"    🗑  Deleted attribution_signals for {startup_name}")

    # 3. Funding events
    db.client.table('funding_events').delete().eq('startup_id', startup_id).execute()
    print(f"    🗑  Deleted funding_events for {startup_name}")

    # 4. Manual overrides (if any)
    db.client.table('manual_overrides').delete().eq('startup_id', startup_id).execute()

    # 5. Startup row itself
    db.client.table('startups').delete().eq('id', startup_id).execute()
    print(f"    🗑  Deleted startup row: {startup_name}")


def main():
    parser = argparse.ArgumentParser(description='Fix bad domain records')
    parser.add_argument('--apply', action='store_true',
                        help='Actually delete and re-process (default is dry-run preview)')
    args = parser.parse_args()

    dry_run = not args.apply

    from app.core.database import DatabaseClient
    db = DatabaseClient()

    print("\n" + "=" * 70)
    print("  BAD DOMAIN CLEANUP")
    print("  Mode:", "DRY RUN (preview only)" if dry_run else "APPLY (will modify database)")
    print("=" * 70)

    # ── Step 1: Find bad startups ─────────────────────────────────────────────
    print("\n📋 Scanning for bad domain records...")
    bad_startups = get_bad_startups(db)

    if not bad_startups:
        print("  ✅ No bad domain records found — nothing to do!")
        return

    print(f"\n  Found {len(bad_startups)} bad startup(s):")
    for s in bad_startups:
        print(f"    • {s['canonical_name']:<25} → {s['website']}")

    if dry_run:
        print("\n  ⚠️  DRY RUN — run with --apply to delete and re-resolve these records.")
        print("\n  Would re-resolve the following companies:")
        for s in bad_startups:
            name = s['canonical_name']
            ctx = COMPANY_CONTEXT.get(name, (None, None, None, None))
            print(f"    • {name}")
            if ctx[0]:
                print(f"        Round: {ctx[0]}, Amount: ${ctx[1]}M" if ctx[1] else f"        Round: {ctx[0]}")
            if ctx[2]:
                print(f"        Description: {ctx[2]}")
        return

    # ── Step 2: Delete bad records ────────────────────────────────────────────
    print(f"\n🗑  Deleting {len(bad_startups)} bad startup records...")
    for s in bad_startups:
        print(f"\n  Deleting: {s['canonical_name']} ({s['website']})")
        delete_startup_completely(db, s['id'], s['canonical_name'])

    print(f"\n  ✅ Deleted {len(bad_startups)} startup records")

    # ── Step 3: Re-resolve and re-attribute ──────────────────────────────────
    print(f"\n🔄 Re-resolving and re-attributing {len(bad_startups)} startups...")
    print("  (Using improved domain resolver with AI search + full context)\n")

    # Build manual entries for the pipeline
    # Skip 'Unknown' — it was never a real company
    entries = []
    skipped = []
    for s in bad_startups:
        name = s['canonical_name']
        if name.lower() == 'unknown':
            print(f"  ⏭  Skipping '{name}' — not a real company name")
            skipped.append(name)
            continue
        ctx = COMPANY_CONTEXT.get(name, (None, None, None, None))
        # Format: (company_name, website_or_None, evidence_urls, investors, founder_bg)
        entries.append((name, None, [], [], []))

    if not entries:
        print("  ⚠️  No valid companies to re-resolve after filtering.")
        return

    print(f"  Re-processing {len(entries)} compan{'y' if len(entries) == 1 else 'ies'}:")
    for name, *_ in entries:
        print(f"    • {name}")

    # Import Pipeline here (after dotenv loaded)
    from pipeline import Pipeline
    p = Pipeline(dry_run=False)  # Save to DB
    run = p.run_manual(entries)

    print(f"\n{'=' * 70}")
    print(f"  CLEANUP COMPLETE")
    print(f"  Deleted:       {len(bad_startups)} bad records ({len(skipped)} skipped as non-real)")
    print(f"  Re-attributed: {run.startups_attributed}")
    print(f"  Errors:        {run.errors_count}")
    print(f"{'=' * 70}\n")


if __name__ == '__main__':
    main()
